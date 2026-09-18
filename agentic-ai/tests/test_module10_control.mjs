import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { canManageControls } from "../module10/deployment/lambda/resilience.mjs";
import { planning } from "../module10/deployment/lambda/planning.mjs";

// Exercise the real handler while replacing only its AWS SDK transport.
const sdk = `
export class DynamoDBClient { async send(command) { return globalThis.__captureSend("ddb", command); } }
export class S3Client { async send(command) { return globalThis.__captureSend("s3", command); } }
export class GetItemCommand { constructor(input) { this.input = input; } }
export class PutItemCommand extends GetItemCommand {}
export class UpdateItemCommand extends GetItemCommand {}
export class GetObjectCommand extends GetItemCommand {}
export class PutObjectCommand extends GetItemCommand {}
export class ListObjectsV2Command extends GetItemCommand {}
`;
registerHooks({
  resolve(specifier, context, next) {
    if (["@aws-sdk/client-dynamodb", "@aws-sdk/client-s3"].includes(specifier))
      return { url: "mock:capture-sdk", shortCircuit: true };
    return next(specifier, context);
  },
  load(url, context, next) {
    if (url === "mock:capture-sdk") return { format: "module", source: sdk, shortCircuit: true };
    return next(url, context);
  },
});
const { handler } = await import("../module10/deployment/lambda/control.mjs");
const body = { session: "browser-run", section: 1, backend: "portkey" };
const event = (payload = body, operator = "true") => ({
  resource: "/control", httpMethod: "POST",
  requestContext: { authorizer: { team: "platform", principalId: "user-1", operator } },
  body: JSON.stringify(payload),
});

test("unauthorized and removed capture-only options never access storage", async () => {
  globalThis.__captureSend = async () => { throw new Error("Unexpected storage access"); };
  assert.equal((await handler(event(body, "false"))).statusCode, 403);
  for (const change of [{ capture_only: true }, { capture_only: false },
    { capture_only: "true" }, { section: true }, { session: "../other" }, { unknown: true }]) {
    assert.equal((await handler(event({ ...body, ...change }))).statusCode, 400);
  }
});

test("legacy control requests retain full-record configuration", async () => {
  const calls = [];
  globalThis.__captureSend = async (_, command) => { calls.push(command); return {}; };
  assert.equal((await handler(event(body))).statusCode, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].constructor.name, "PutItemCommand");
  assert.ok(calls[0].input.Item.revision.S);
});

test("current and legacy trusted roles work without overriding an explicit denial", async () => {
  assert.equal(canManageControls({ presenter: "true" }), true);
  for (const auth of [{ operator: "false", presenter: "true" }, { presenter: true }, {}, { operator: true }])
    assert.equal(canManageControls(auth), false);
  const request = event();
  request.requestContext.authorizer = { team: "platform", principalId: "user-1", presenter: "true" };
  globalThis.__captureSend = async () => ({});
  assert.equal((await handler(request)).statusCode, 200);
  let read = false;
  const state = await planning({ ...request, httpMethod: "GET", body: undefined }, async () => {
    read = true;
    return { document: { flags: { "module10-recovery-planning": { on: false, version: 1 } } }, revision: "r1" };
  }, () => assert.fail("GET cannot write"));
  assert.equal(read, true);
  assert.equal(state.body.can_edit, true);
});
