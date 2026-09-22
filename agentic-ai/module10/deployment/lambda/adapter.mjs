import { createHash, randomUUID } from "node:crypto";
import { once } from "node:events";
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient, GetItemCommand, PutItemCommand, UpdateItemCommand, DeleteItemCommand } from "@aws-sdk/client-dynamodb";
import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";
import { corsHeaders } from "./cors.mjs";
import { sharedFaultEnabled } from "./resilience.mjs";

const database = new DynamoDBClient({});
const core = new BedrockAgentCoreClient({ maxAttempts: 1 });
const s3 = new S3Client({});
const table = () => process.env.TABLE_NAME;
const identifier = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);

export function validateRequest(event) {
  const identity = event.requestContext?.authorizer;
  if (!identity || !identifier(identity.principalId) || !identifier(identity.team)) throw Object.assign(new Error("Unauthorized"), { status: 403 });
  if (Object.keys(event.headers || {}).some((h) => ["x-demo-team", "x-demo-user"].includes(h.toLowerCase()))) {
    throw Object.assign(new Error("Caller identity fields are not accepted"), { status: 400 });
  }
  if (event.isBase64Encoded || typeof event.body !== "string" || Buffer.byteLength(event.body) > 16384) {
    throw Object.assign(new Error("Invalid request body"), { status: 413 });
  }
  let body;
  try { body = JSON.parse(event.body); } catch { throw Object.assign(new Error("Invalid JSON"), { status: 400 }); }
  if (!body || Array.isArray(body) || Object.keys(body).some((k) => !["change_summary", "request_id"].includes(k))
      || typeof body.change_summary !== "string" || !body.change_summary.trim() || body.change_summary.length > 4000
      || (body.request_id !== undefined && !identifier(body.request_id))) {
    throw Object.assign(new Error("Invalid invocation request"), { status: 400 });
  }
  return {
    identity: { team: identity.team, principal: identity.principalId },
    request: { change_summary: body.change_summary.trim(), request_id: body.request_id || randomUUID() },
  };
}

function jsonReply(stream, statusCode, body) {
  const response = awslambda.HttpResponseStream.from(stream, {
    statusCode, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...corsHeaders() },
  });
  // A first write emits Lambda's HTTP integration prelude. end(body) alone
  // bypasses that hook and API Gateway cannot parse the response metadata.
  response.write(JSON.stringify(body));
  response.end();
}

async function run(event, rawStream) {
  let envelope;
  try { envelope = validateRequest(event); }
  catch (error) { jsonReply(rawStream, error.status || 400, { error: error.message }); return; }
  const { team, principal } = envelope.identity;
  const requestId = envelope.request.request_id;
  const pk = `TEAM#${team}#USER#${principal}`;
  const key = { pk: { S: pk }, sk: { S: `REQUEST#${requestId}` } };
  const hash = createHash("sha256").update(envelope.request.change_summary).digest("hex");
  const sessionId = randomUUID();
  const now = Math.floor(Date.now() / 1000);
  const leaseKey = { pk: { S: `TEAM#${team}` }, sk: { S: "ACTIVE" } };
  let lease = false;
  let admitted = false;
  let stream;
  try {
    const previous = (await database.send(new GetItemCommand({ TableName: table(), Key: key, ConsistentRead: true }))).Item;
    if (previous) {
      if (previous.requestHash.S !== hash) {
        jsonReply(rawStream, 409, { error: "Request ID already used for a different request" }); return;
      }
      jsonReply(rawStream, 202, { request_id: requestId, status: previous.status.S, result_url: `requests/${requestId}` }); return;
    }
    try {
      await database.send(new PutItemCommand({
        TableName: table(), Item: { ...leaseKey, owner: { S: sessionId }, expiresAt: { N: String(now + 300) } },
        ConditionExpression: "attribute_not_exists(pk) OR expiresAt < :now",
        ExpressionAttributeValues: { ":now": { N: String(now) } },
      }));
      lease = true;
    } catch (error) {
      if (error.name !== "ConditionalCheckFailedException") throw error;
      jsonReply(rawStream, 429, { error: "Team already has an active investigation", source: "team-concurrency" }); return;
    }
    try {
      await database.send(new PutItemCommand({ TableName: table(),
        Item: { ...key, requestHash: { S: hash }, sessionId: { S: sessionId }, status: { S: "RUNNING" },
          expiresAt: { N: String(now + 86400) }, deadline: { N: String(now + 240) } },
        ConditionExpression: "attribute_not_exists(pk)",
      }));
      admitted = true;
    } catch (error) {
      if (error.name !== "ConditionalCheckFailedException") throw error;
      jsonReply(rawStream, 409, { error: "Request already admitted", request_id: requestId }); return;
    }
    // Shared operator configuration is trusted and never accepted in the body.
    const demo = (await database.send(new GetItemCommand({
      TableName: table(), Key: { pk: { S: "DEMO" }, sk: { S: "CONFIG" } }, ConsistentRead: true,
    }))).Item;
    envelope.context = {
      capture_session: demo?.session?.S || "cloud-demo",
      section: Number(demo?.section?.N || 0),
      model_backend: "portkey",
    };
    const xray = Object.fromEntries((process.env._X_AMZN_TRACE_ID || "").split(";").map((p) => p.split("=")));
    if (/^1-[a-f0-9]{8}-[a-f0-9]{24}$/.test(xray.Root || "") && /^[a-f0-9]{16}$/.test(xray.Parent || "")) {
      envelope.context.traceparent = `00-${xray.Root.slice(2).replace("-", "")}-${xray.Parent}-${xray.Sampled === "1" ? "01" : "00"}`;
    }
    if (event.requestContext.authorizer.operator === "true" && demo?.drillRequest?.S === requestId) {
      await database.send(new UpdateItemCommand({ TableName: table(),
        Key: { pk: { S: "DEMO" }, sk: { S: "CONFIG" } }, UpdateExpression: "REMOVE drillRequest",
        ConditionExpression: "drillRequest = :request", ExpressionAttributeValues: { ":request": { S: requestId } },
      }));
      envelope.context.drill = true;
    }
    if (sharedFaultEnabled(demo, event.requestContext.authorizer)) envelope.context.drill = true;
    const response = await core.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: process.env.RUNTIME_ARN, qualifier: process.env.RUNTIME_ENDPOINT,
      runtimeSessionId: sessionId, contentType: "application/json", accept: "text/event-stream",
      payload: Buffer.from(JSON.stringify(envelope)),
    }), { abortSignal: AbortSignal.timeout(195_000) });
    stream = awslambda.HttpResponseStream.from(rawStream, {
      statusCode: 200, headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store",
        "X-Request-Id": requestId, ...corsHeaders() },
    });
    for await (const chunk of response.response) {
      if (!stream.write(chunk)) await once(stream, "drain");
    }
    // Persisted agent outcome, rather than HTTP 200, determines completion.
    const result = await s3.send(new GetObjectCommand({
      Bucket: process.env.ARTIFACT_BUCKET, Key: `results/${team}/${principal}/${requestId}.json`,
    }));
    const body = JSON.parse(await result.Body.transformToString());
    await database.send(new UpdateItemCommand({
      TableName: table(), Key: key, UpdateExpression: "SET #s = :s",
      ExpressionAttributeNames: { "#s": "status" },
      ExpressionAttributeValues: { ":s": { S: body.status === "completed" ? "COMPLETED" : "FAILED" } },
    }));
    console.log(JSON.stringify({ event: "invocation_complete", request_id: requestId,
      gateway_request_id: event.requestContext.requestId, team, status: body.status, trace_id: body.trace_id }));
    stream.end();
  } catch (error) {
    if (admitted) {
      await database.send(new UpdateItemCommand({
        TableName: table(), Key: key, UpdateExpression: "SET #s = :s",
        ExpressionAttributeNames: { "#s": "status" }, ExpressionAttributeValues: { ":s": { S: "FAILED" } },
      })).catch(() => {});
    }
    console.log(JSON.stringify({ event: "invocation_failed", request_id: requestId, error_type: error.name }));
    if (stream) {
      stream.write(`event: error\ndata: ${JSON.stringify({ status: "failed", request_id: requestId, error: "Invocation failed" })}\n\n`);
      stream.end();
    } else jsonReply(rawStream, 502, { error: "Invocation failed", request_id: requestId });
  } finally {
    if (lease) await database.send(new DeleteItemCommand({
      TableName: table(), Key: leaseKey, ConditionExpression: "#owner = :owner",
      ExpressionAttributeNames: { "#owner": "owner" }, ExpressionAttributeValues: { ":owner": { S: sessionId } },
    })).catch(() => {});
  }
}

export const handler = globalThis.awslambda ? awslambda.streamifyResponse(run) : run;
