import test from "node:test";
import assert from "node:assert/strict";
import { resilience, resilienceState, resilienceUpdate, sharedFaultEnabled } from "../module10/deployment/lambda/resilience.mjs";

const event = (body, operator = "true") => ({
  httpMethod: body === undefined ? "GET" : "POST",
  requestContext: { authorizer: { operator } }, body: JSON.stringify(body),
});
const denied = () => { throw new Error("Storage must not be accessed"); };

test("initial and existing configurations expose only simulator, routing, and revision", async () => {
  assert.deepEqual(resilienceState(), { enabled: false, backend: "portkey", revision: "initial" });
  assert.equal(resilienceState({ backend: { S: "bedrock" } }).backend, "portkey");
  const state = resilienceState({
    modelFaultEnabled: { BOOL: true }, backend: { S: "portkey" }, revision: { S: "r1" },
    session: { S: "private-session" }, drillRequest: { S: "private-request" },
  });
  assert.deepEqual(await resilience(event(undefined, "false"), async () => state, denied),
    { statusCode: 200, body: { enabled: true, backend: "portkey", revision: "r1", can_edit: false } });
});

test("non-operators and malformed updates never reach storage", async () => {
  for (const operator of ["false", true, undefined]) {
    const request = event({ enabled: true, revision: "r1" });
    request.requestContext.authorizer.operator = operator;
    assert.equal((await resilience(request, denied, denied)).statusCode, 403);
  }
  for (const body of [null, [], {}, { enabled: "true", revision: "r1" },
    { enabled: true, revision: "" }, { enabled: true, revision: "r".repeat(129) },
    { enabled: true, revision: "r1", backend: "arbitrary" }]) {
    assert.equal((await resilience(event(body), denied, denied)).statusCode, 400);
  }
  assert.equal((await resilience({ ...event(), httpMethod: "DELETE" }, denied, denied)).statusCode, 405);
});

test("routing is always enabled while the simulator can be turned on and off", async () => {
  let state = resilienceState(), writes = 0;
  const read = async () => state;
  const write = async (enabled, revision) => {
    assert.equal(revision, state.revision);
    state = { enabled, backend: "portkey", revision: `r${++writes}` };
    return state;
  };
  for (const enabled of [false, true, false]) {
    const result = await resilience(event({ enabled, revision: state.revision }), read, write);
    assert.equal(result.statusCode, 200);
    assert.equal(result.body.backend, "portkey");
    assert.equal(result.body.enabled, enabled);
  }
  assert.equal(writes, 2);
  await resilience(event({ enabled: false, revision: state.revision }), read, write);
  assert.equal(writes, 2, "unchanged state does not write");
});

test("stale and racing updates cannot overwrite newer UI or CLI configuration", async () => {
  const read = async () => ({ enabled: false, backend: "portkey", revision: "new" });
  assert.equal((await resilience(event({ enabled: true, revision: "old" }), read, denied)).statusCode, 409);
  const write = async () => { throw Object.assign(new Error("Race"), { name: "ConditionalCheckFailedException" }); };
  assert.equal((await resilience(event({ enabled: true, revision: "new" }), read, write)).statusCode, 409);
});

test("DynamoDB patch preserves CLI scene fields and conditionally rotates the revision", () => {
  const update = resilienceUpdate("table", true, "r1");
  assert.equal(update.ConditionExpression, "revision = :expected");
  assert.deepEqual(update.ExpressionAttributeValues[":expected"], { S: "r1" });
  assert.deepEqual(update.ExpressionAttributeValues[":backend"], { S: "portkey" });
  assert.deepEqual(update.ExpressionAttributeValues[":enabled"], { BOOL: true });
  assert.notEqual(update.ExpressionAttributeValues[":next"].S, "r1");
  assert.doesNotMatch(update.UpdateExpression, /session|section|drillRequest/);
  const initial = resilienceUpdate("table", false, "initial");
  assert.equal(initial.ConditionExpression, "attribute_not_exists(revision)");
  assert.equal(initial.ExpressionAttributeValues[":expected"], undefined);
});

test("shared fault requires trusted operator permission and a boolean flag, including legacy configuration", () => {
  const config = { backend: { S: "portkey" }, modelFaultEnabled: { BOOL: true } };
  assert.equal(sharedFaultEnabled(config, { operator: "true" }), true);
  for (const operator of [true, "false", undefined])
    assert.equal(sharedFaultEnabled(config, { operator }), false);
  assert.equal(sharedFaultEnabled({ ...config, backend: { S: "bedrock" } }, { operator: "true" }), true);
  for (const item of [undefined, {},
    { ...config, modelFaultEnabled: { BOOL: false } }, { ...config, modelFaultEnabled: { S: "true" } }])
    assert.equal(sharedFaultEnabled(item, { operator: "true" }), false);
});
