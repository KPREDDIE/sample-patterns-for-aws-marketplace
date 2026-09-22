import test from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { consumeEvents } from "../module10/ui/stream.mjs";
import { detailSections } from "../module10/ui/details.mjs";
import { verifyToken } from "../module10/deployment/lambda/jwt.mjs";
function stream(parts) {
  return new ReadableStream({ start(controller) {
    for (const part of parts) controller.enqueue(part);
    controller.close();
  } });
}
const encode = text => new TextEncoder().encode(text);
test("SSE parses arbitrary byte boundaries, Unicode, CRLF, and multiline data", async () => {
  const bytes = encode('event: accepted\r\ndata: {"request_id":"a"}\r\n\r\n: heartbeat\n\nevent: result\ndata: {"status":"completed",\ndata: "review":"✓ café"}\n\n');
  for (let split = 1; split < bytes.length; split++) {
    const seen = [];
    const result = await consumeEvents(stream([bytes.slice(0, split), bytes.slice(split)]), e => seen.push(e));
    assert.equal(result.data.review, "✓ café");
    assert.deepEqual(seen.map(e => e.event), ["accepted", "result"]);
  }
});
test("an open stream reports acceptance before the final answer is available", async () => {
  let control;
  const live = new ReadableStream({ start(c) { control = c; } });
  const seen = [];
  const completion = consumeEvents(live, e => seen.push(e.event));
  control.enqueue(encode('event: accepted\ndata: {}\n\n'));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(seen, ["accepted"]);
  control.enqueue(encode('event: result\ndata: {"status":"completed"}\n\n'));
  control.close(); await completion;
});
test("a dropped stream cannot masquerade as a completed investigation", async () => {
  await assert.rejects(consumeEvents(stream([encode('event: accepted\ndata: {}\n\n')]), () => {}),
    /without a terminal result/);
});
test("terminal failure is retained and malformed or repeated terminal events fail", async () => {
  assert.equal((await consumeEvents(stream([encode('event: error\ndata: {"status":"failed"}\n\n')]), () => {})).event, "error");
  await assert.rejects(consumeEvents(stream([encode('event: result\ndata: not-json\n\n')]), () => {}));
  await assert.rejects(consumeEvents(stream([encode('event: result\ndata: {}\n\nevent: result\ndata: {}\n\n')]), () => {}), /after terminal/);
});
test("details distinguish disabled, unavailable, execution, and simulated attempts", () => {
  const sections = detailSections({
    capabilities: { recovery_planning: false }, events: [], release: { version: 7 },
    routing: { fallback_used: true }, model_calls: [{ agent: "recovery-planner", model: "fallback",
      attempts: [{ model: "primary", status_code: 429, source: "injected" },
        { model: "fallback", status_code: 200, source: "bedrock" }] }],
  }, { started: "2026-09-19T00:00:00Z" });
  const flags = sections.find(s => s.title.startsWith("LaunchDarkly")).rows;
  assert.deepEqual(flags.find(r => r[0] === "Recovery planning"), ["Recovery planning", "Disabled"]);
  assert.deepEqual(flags.find(r => r[0] === "Investigation"), ["Investigation", "Unavailable"]);
  assert.deepEqual(flags.find(r => r[0] === "Planner executed"), ["Planner executed", "Yes"]);
  assert.match(JSON.stringify(sections), /SIMULATED/);
  const attempts = sections.find(s => s.title === "Models & routing").rows.filter(r => r[0].startsWith("Attempt "));
  assert.equal(attempts[0][2], "attempt-error");
  assert.equal(attempts[1][2], undefined);
  assert.doesNotMatch(JSON.stringify(sections), /2026|Submitted|First event|Duration/);
  assert.deepEqual(sections.find(s => s.title === "Reported usage").rows[0], ["Input tokens", "Unavailable"]);
  const executed = detailSections({ events: [{ agent: "Recovery Planner", tool: "get_blast_radius" }] }, {});
  assert.deepEqual(executed[1].rows.find(r => r[0] === "Planner executed"), ["Planner executed", "Yes"]);
  assert.deepEqual(detailSections({ events: [], model_calls: [] }, {})[1].rows.find(r => r[0] === "Planner executed"),
    ["Planner executed", "No"]);
});

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...publicKey.export({ format: "jwk" }), kid: "test-key" };
function token(overrides = {}) {
  const header = Buffer.from(JSON.stringify({ alg: "RS256", kid: jwk.kid })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({ iss: "https://issuer.example", client_id: "browser",
    token_use: "access", exp: 200, sub: "principal", scope: "openid module10/invoke", ...overrides })).toString("base64url");
  const input = `${header}.${payload}`;
  return `${input}.${sign("RSA-SHA256", Buffer.from(input), privateKey).toString("base64url")}`;
}
const options = { issuer: "https://issuer.example", clientIds: ["browser", "terminal"], now: 100, getKeys: async () => [jwk] };
test("browser and existing terminal tokens work; other clients and invalid claims are denied", async () => {
  for (const client_id of options.clientIds) assert.equal((await verifyToken(token({ client_id }), options)).client_id, client_id);
  for (const changes of [{ client_id: "other" }, { exp: 100 }, { scope: "openid" }, { token_use: "id" }, { iss: "https://other" }]) {
    await assert.rejects(verifyToken(token(changes), options));
  }
  const parts = token().split(".");
  parts[1] = Buffer.from(JSON.stringify({ sub: "forged" })).toString("base64url");
  await assert.rejects(verifyToken(parts.join("."), options));
});
