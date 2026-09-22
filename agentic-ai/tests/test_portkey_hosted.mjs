import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { startGateway } from "../module10/gateway/launcher.mjs";
import { hostedPolicy } from "../module10/gateway/hosted.mjs";

test("real hosted Portkey isolates authentication, drills, routing and console evidence", async () => {
  const serviceToken = "s".repeat(48), adminToken = "p".repeat(48);
  const evidence = new Map(), upstream = [];
  let credentialCalls = 0;
  const policy = hostedPolicy({
    serviceToken, adminToken, region: "us-test-1",
    routing: JSON.parse(await readFile(new URL("../module10/gateway/routing.json", import.meta.url))),
    credentials: async () => {
      credentialCalls++;
      return { accessKeyId: `FAKE${credentialCalls}`, secretAccessKey: "secret", sessionToken: "session" };
    },
    putEvidence: async (key, value) => evidence.set(key, value),
  });
  const gateway = await startGateway(resolve("module10/.cache/portkey"), {
    hosted: { ...policy, port: 0 },
    upstreamFetch: async (url, options) => {
      upstream.push({ url, headers: options.headers, body: JSON.parse(options.body) });
      return Response.json({ id: "test", model: JSON.parse(options.body).model, output: [] });
    },
  });
  const request = (id, drill = false, token = serviceToken, extra = {}) => fetch(gateway.baseUrl + "/v1/openai/v1/responses", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`, "content-type": "application/json",
      "x-portkey-trace-id": id, "x-module10-timeout-ms": "1000",
      "x-module10-drill": String(drill),
      "x-portkey-metadata": JSON.stringify({ agent_role: "recovery-planner", evidence_ready: "true",
        team: "platform", principal: "operator", request_id: id }),
      ...extra,
    },
    body: JSON.stringify({ model: "recovery-planner", input: "hello", store: false }),
  });
  try {
    assert.equal((await request("bad", false, adminToken)).status, 401);
    assert.equal((await request("override", false, serviceToken, { "x-portkey-config": "{}" })).status, 400);
    assert.equal(credentialCalls, 0);
    const results = await Promise.all([request("drill", true), request("normal")]);
    for (const response of results) {
      assert.equal(response.status, 200, await response.clone().text());
      assert.ok(evidence.has(response.headers.get("x-module10-evidence-key")));
    }
    assert.equal(JSON.parse(results[0].headers.get("x-module10-attempts")).length, 2);
    assert.equal(JSON.parse(results[1].headers.get("x-module10-attempts")).length, 1);
    assert.equal(credentialCalls, 2);
    assert.equal(upstream.length, 2);
    assert.ok(upstream.every(call => !JSON.stringify(call.headers).includes(serviceToken)));
    assert.ok(!JSON.stringify([...evidence]).includes("secret"));
    assert.equal((await fetch(gateway.baseUrl + "/__demo/drill", { method: "POST" })).status, 404);
    assert.equal((await fetch(gateway.baseUrl + "/log/stream", {
      headers: { authorization: `Bearer ${serviceToken}` },
    })).status, 401);
    assert.equal((await fetch(gateway.baseUrl + "/public/auth", {
      method: "POST", body: JSON.stringify({ admin_token: serviceToken }),
    })).status, 401);
    const login = await fetch(gateway.baseUrl + "/public/auth", {
      method: "POST", body: JSON.stringify({ admin_token: adminToken }),
    });
    assert.equal(login.status, 200);
    assert.match(login.headers.get("set-cookie"), /Secure/);
    const stream = await fetch(gateway.baseUrl + "/log/stream", {
      headers: { authorization: `Bearer ${adminToken}` },
    });
    assert.equal(stream.status, 200);
    const reader = stream.body.getReader();
    let content = "";
    while (!content.includes('"request_id":"normal"')) content += new TextDecoder().decode((await reader.read()).value);
    assert.match(content, /"request_id":"drill"/);
    await reader.cancel();
  } finally {
    await gateway.close();
  }
});
