import { randomUUID } from "node:crypto";
import { DynamoDBClient, GetItemCommand, PutItemCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { S3Client, GetObjectCommand, PutObjectCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import { corsHeaders } from "./cors.mjs";
import { planning } from "./planning.mjs";
import { resilience, resilienceState, resilienceUpdate, canManageControls } from "./resilience.mjs";
const database = new DynamoDBClient({});
const s3 = new S3Client({});
const reply = (statusCode, body) => ({ statusCode, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...corsHeaders() }, body: JSON.stringify(body) });
const identifier = (s) => typeof s === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(s);

export async function handler(event) {
  const auth = event.requestContext?.authorizer;
  if (!identifier(auth?.team) || !identifier(auth?.principalId)) return reply(403, { error: "Forbidden" });
  try {
    if (event.resource === "/resilience") {
      const result = await resilience(event, async () => {
        const { Item } = await database.send(new GetItemCommand({
          TableName: process.env.TABLE_NAME,
          Key: { pk: { S: "DEMO" }, sk: { S: "CONFIG" } }, ConsistentRead: true,
        }));
        return resilienceState(Item);
      }, async (enabled, revision) => {
        const { Attributes } = await database.send(new UpdateItemCommand(
          resilienceUpdate(process.env.TABLE_NAME, enabled, revision)));
        return resilienceState(Attributes);
      });
      return reply(result.statusCode, result.body);
    }
    if (event.resource === "/planning") {
      const key = { Bucket: process.env.ARTIFACT_BUCKET, Key: "config/flags.json" };
      const result = await planning(event, async () => {
        const object = await s3.send(new GetObjectCommand(key));
        return { document: JSON.parse(await object.Body.transformToString()), revision: object.ETag };
      }, async (document, revision) => {
        const object = await s3.send(new PutObjectCommand({ ...key,
          Body: JSON.stringify(document), ContentType: "application/json", IfMatch: revision }));
        return object.ETag;
      });
      return reply(result.statusCode, result.body);
    }
    if (event.httpMethod === "GET") {
      if (event.resource === "/me") return reply(200, { team: auth.team, principal: auth.principalId });
      const id = event.pathParameters?.id;
      if (!identifier(id)) return reply(400, { error: "Invalid request ID" });
      if (event.resource.startsWith("/captures/")) {
        if (!canManageControls(auth)) return reply(403, { error: "Operator permission required" });
        const objects = await s3.send(new ListObjectsV2Command({
          Bucket: process.env.ARTIFACT_BUCKET, Prefix: `manifests/${id}/`, MaxKeys: 100,
        }));
        if (objects.IsTruncated) return reply(413, { error: "Capture exceeds demo retrieval limit" });
        const records = await Promise.all((objects.Contents || []).map(async ({ Key }) => {
          const object = await s3.send(new GetObjectCommand({ Bucket: process.env.ARTIFACT_BUCKET, Key }));
          return JSON.parse(await object.Body.transformToString());
        }));
        return reply(200, { records });
      }
      const { Item } = await database.send(new GetItemCommand({
        TableName: process.env.TABLE_NAME,
        Key: { pk: { S: `TEAM#${auth.team}#USER#${auth.principalId}` }, sk: { S: `REQUEST#${id}` } }, ConsistentRead: true,
      }));
      if (!Item) return reply(404, { error: "Request not found" });
      // Runtime may have completed even when the client disconnected.
      try {
        const result = await s3.send(new GetObjectCommand({ Bucket: process.env.ARTIFACT_BUCKET,
          Key: `results/${auth.team}/${auth.principalId}/${id}.json` }));
        return reply(200, JSON.parse(await result.Body.transformToString()));
      } catch (error) {
        if (!["NoSuchKey", "AccessDenied"].includes(error.name)) throw error;
        return reply(200, { request_id: id, status: Item.status.S === "RUNNING" && Number(Item.deadline.N) < Date.now() / 1000 ? "TIMED_OUT" : Item.status.S });
      }
    }
    if (!canManageControls(auth)) return reply(403, { error: "Operator permission required" });
    let body;
    try { body = JSON.parse(event.body); } catch { return reply(400, { error: "Invalid JSON" }); }
    if (!body || Array.isArray(body) || Object.keys(body).some((k) => !["session", "section", "backend", "drill_request", "planning_enabled"].includes(k))
        || typeof body.session !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(body.session)
        || ![0, 1, 2].includes(body.section) || !["bedrock", "portkey"].includes(body.backend)
        || (body.drill_request !== undefined && !identifier(body.drill_request))
        || (body.planning_enabled !== undefined && typeof body.planning_enabled !== "boolean")) return reply(400, { error: "Invalid demo configuration" });
    body.backend = "portkey";
    if (body.planning_enabled !== undefined) {
      const object = await s3.send(new GetObjectCommand({ Bucket: process.env.ARTIFACT_BUCKET, Key: "config/flags.json" }));
      const flags = JSON.parse(await object.Body.transformToString());
      flags.flags["module10-recovery-planning"].on = body.planning_enabled;
      flags.flags["module10-recovery-planning"].version += 1;
      await s3.send(new PutObjectCommand({ Bucket: process.env.ARTIFACT_BUCKET, Key: "config/flags.json",
        Body: JSON.stringify(flags), ContentType: "application/json", IfMatch: object.ETag }));
    }
    await database.send(new PutItemCommand({
      TableName: process.env.TABLE_NAME, Item: {
        pk: { S: "DEMO" }, sk: { S: "CONFIG" }, session: { S: body.session },
        section: { N: String(body.section) }, backend: { S: body.backend },
        revision: { S: randomUUID() },
        ...(body.drill_request ? { drillRequest: { S: body.drill_request } } : {}),
      },
    }));
    return reply(200, { status: "configured", ...body });
  } catch (error) {
    console.log(JSON.stringify({ event: "control_failed", error_type: error.name }));
    return reply(500, { error: "Operation failed" });
  }
}
