import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { planning } from "../module10/deployment/lambda/planning.mjs";
import { planningControl } from "../module10/ui/planning.mjs";
import { resilienceControl } from "../module10/ui/resilience.mjs";

const fixture = JSON.parse(await readFile(new URL("../module10/flags.json", import.meta.url)));
const event = (body, operator = "true") => ({
  httpMethod: body === undefined ? "GET" : "POST",
  requestContext: { authorizer: { operator } }, body: JSON.stringify(body),
});
function storage() {
  let document = structuredClone(fixture), revision = '"first"', writes = 0;
  return {
    read: async () => ({ document: structuredClone(document), revision }),
    write: async (next, expected) => {
      assert.equal(expected, revision);
      document = structuredClone(next); revision = '"next"'; writes++;
      return revision;
    },
    get writes() { return writes; },
  };
}
test("status reads current shared state and operator permission without writes", async () => {
  const s = storage();
  assert.deepEqual(await planning(event(undefined, "false"), s.read, s.write),
    { statusCode: 200, body: { enabled: false, revision: '"first"', can_edit: false } });
  assert.equal(s.writes, 0);
});
test("non-operators and malformed updates are rejected before storage access", async () => {
  const denied = () => { throw new Error("Storage must not be accessed"); };
  for (const operator of ["false", true, undefined]) {
    const request = event({ enabled: true, revision: '"first"' });
    request.requestContext.authorizer.operator = operator;
    assert.equal((await planning(request, denied, denied)).statusCode, 403);
  }
  for (const body of [null, [], {}, { enabled: "true", revision: "r" },
    { enabled: true, revision: "" }, { enabled: true, revision: "r", backend: "portkey" }]) {
    assert.equal((await planning(event(body), denied, denied)).statusCode, 400);
  }
});
test("toggle preserves team targeting and every setting except on/version", async () => {
  const s = storage();
  const result = await planning(event({ enabled: true, revision: '"first"' }), s.read, s.write);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.enabled, true);
  assert.equal(result.body.revision, '"next"');
  const expected = structuredClone(fixture);
  expected.flags["module10-recovery-planning"].on = true;
  expected.flags["module10-recovery-planning"].version++;
  assert.deepEqual((await s.read()).document, expected);
  assert.equal((await planning(event(), s.read, s.write)).body.enabled, true);
  await planning(event({ enabled: true, revision: '"next"' }), s.read, s.write);
  assert.equal(s.writes, 1, "already-correct state should not write");
});
test("stale browser revision cannot overwrite another operator's change", async () => {
  const s = storage();
  assert.equal((await planning(event({ enabled: true, revision: '"old"' }), s.read, s.write)).statusCode, 409);
  assert.equal(s.writes, 0);
  for (const name of ["PreconditionFailed", "ConditionalRequestConflict"]) {
    const racingWrite = async () => { throw Object.assign(new Error("Concurrent write"), { name }); };
    assert.equal((await planning(event({ enabled: true, revision: '"first"' }), s.read, racingWrite)).statusCode, 409);
  }
});

// Minimal DOM boundary for exercising the real asynchronous controller without
// a browser dependency. No timers or HTTP responses escape this test harness.
function ui(t, api, factory = planningControl, prefix = "planning") {
  const elements = new Map();
  for (const id of ["panel", "state", "feedback", "toggle"].map(s => `${prefix}-${s}`)) {
    const classes = new Set();
    elements.set(id, {
      hidden: true, disabled: true, textContent: "", classList: { add: s => classes.add(s), remove: s => classes.delete(s) },
      addEventListener(name, callback) { this[name] = callback; },
    });
  }
  const previous = globalThis.document;
  globalThis.document = { getElementById: id => elements.get(id) };
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const control = factory(api);
  t.after(() => { control.stop(); globalThis.document = previous; });
  return { control, get: id => elements.get(prefix + "-" + id) };
}
const settled = () => new Promise(resolve => setImmediate(resolve));
const ok = body => ({ ok: true, status: 200, json: async () => body });
const flagState = (enabled = false, revision = "r1", can_edit = true) => ({ enabled, revision, can_edit });

test("UI refreshes external changes every five seconds and hides controls for viewers", async t => {
  let shared = flagState(false, "r1", false), reads = 0;
  const { control, get } = ui(t, async () => { reads++; return ok(shared); });
  control.start(); await settled();
  assert.equal(get("state").textContent, "Shared flag: Off");
  assert.equal(get("toggle").hidden, true);
  shared = flagState(true, "r2", false);
  t.mock.timers.tick(4999); await settled(); assert.equal(reads, 1);
  t.mock.timers.tick(1); await settled();
  assert.equal(get("state").textContent, "Shared flag: On");
  assert.equal(reads, 2);
});
test("UI toggle sends an explicit desired state/revision and ignores an older in-flight poll", async t => {
  let reads = 0, release, post;
  const { control, get } = ui(t, async (_path, body) => {
    if (body) { post = body; return ok(flagState(true, "r2")); }
    if (++reads === 1) return ok(flagState());
    return new Promise(resolve => { release = () => resolve(ok(flagState())); });
  });
  control.start(); await settled();
  t.mock.timers.tick(5000); await settled();
  const update = get("toggle").click();
  assert.equal(get("toggle").disabled, true);
  await update;
  assert.deepEqual(post, { enabled: true, revision: "r1" });
  release(); await settled();
  assert.equal(get("state").textContent, "Shared flag: On");
  assert.equal(get("toggle").textContent, "Turn off");
  assert.equal(get("toggle").disabled, false);
});
test("failed status or unconfirmed writes show unavailable until the next successful poll", async t => {
  let failRead = true;
  const { control, get } = ui(t, async (_path, body) => {
    if (failRead || body) throw new Error("Network error");
    return ok(flagState());
  });
  control.start(); await settled();
  assert.equal(get("state").textContent, "Shared flag: unavailable");
  assert.equal(get("toggle").disabled, true);
  failRead = false;
  t.mock.timers.tick(5000); await settled();
  assert.equal(get("state").textContent, "Shared flag: Off");
  await get("toggle").click();
  assert.equal(get("state").textContent, "Shared flag: unavailable");
  assert.equal(get("toggle").disabled, true);
  t.mock.timers.tick(5000); await settled();
  assert.equal(get("toggle").disabled, false);
});
test("a conflict refreshes authoritative state without automatically retrying the write", async t => {
  let shared = flagState(), writes = 0;
  const { control, get } = ui(t, async (_path, body) => {
    if (!body) return ok(shared);
    writes++; shared = flagState(true, "r2");
    return { ok: false, status: 409 };
  });
  control.start(); await settled();
  await get("toggle").click();
  assert.equal(writes, 1);
  assert.equal(get("state").textContent, "Shared flag: On");
  assert.match(get("feedback").textContent, /changed elsewhere/);
  assert.equal(get("toggle").disabled, false);
  t.mock.timers.tick(5000); await settled(); assert.equal(writes, 1);
});
test("sign-out discards pending status responses and stops polling", async t => {
  let release, reads = 0;
  const { control, get } = ui(t, async () => {
    reads++;
    return new Promise(resolve => { release = () => resolve(ok(flagState(true))); });
  });
  control.start(); await settled();
  control.stop(); release(); await settled();
  t.mock.timers.tick(10000); await settled();
  assert.equal(get("panel").hidden, true);
  assert.equal(get("toggle").hidden, true);
  assert.equal(reads, 1);
});

test("resilience UI directly toggles fault injection without a Portkey enable step", async t => {
  let shared = { ...flagState(), backend: "portkey" };
  const writes = [];
  const { control, get } = ui(t, async (path, body) => {
    assert.equal(path, "/resilience");
    if (body) {
      writes.push(body);
      shared = { ...shared, ...body, backend: "portkey", revision: `r${writes.length + 1}` };
    }
    return ok(shared);
  }, resilienceControl, "resilience");
  control.start(); await settled();
  assert.equal(get("state").textContent, "Fault injection: Off");
  assert.equal(get("feedback").textContent, "");
  assert.equal(get("toggle").textContent, "Turn on");
  await get("toggle").click();
  assert.equal(get("state").textContent, "Fault injection: On");
  assert.equal(get("toggle").textContent, "Turn off");
  await get("toggle").click();
  assert.equal(get("state").textContent, "Fault injection: Off");
  assert.deepEqual(writes, [
    { enabled: true, revision: "r1" }, { enabled: false, revision: "r2" },
  ]);
});

test("resilience UI polls CLI resets and shows state without edit controls to viewers", async t => {
  let shared = { ...flagState(true, "r1", false), backend: "portkey" };
  const { control, get } = ui(t, async () => ok(shared), resilienceControl, "resilience");
  control.start(); await settled();
  assert.equal(get("toggle").hidden, true);
  assert.equal(get("state").textContent, "Fault injection: On");
  shared = { ...flagState(false, "r2", false), backend: "portkey" };
  t.mock.timers.tick(5000); await settled();
  assert.equal(get("state").textContent, "Fault injection: Off");
});
