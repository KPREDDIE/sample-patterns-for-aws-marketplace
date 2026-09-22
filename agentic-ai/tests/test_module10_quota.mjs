import test from "node:test";
import assert from "node:assert/strict";
import { isMetered } from "../module10/deployment/lambda/quota.mjs";
import { responseMessage } from "../module10/ui/http.mjs";

const arn = route => `arn:aws:execute-api:TEST_REGION:TEST_ACCOUNT:api/demo/${route}`;
test("only shared-control status polls omit the authorizer's usage key", () => {
  assert.equal(isMetered(arn("GET/planning")), false);
  assert.equal(isMetered(arn("GET/resilience")), false);
  for (const route of ["POST/planning", "POST/resilience", "POST/invoke", "POST/control", "GET/me", "GET/probe",
    "GET/requests/request-1", "GET/other/GET/planning", "GET/planning/extra", "GET/resilience/extra"]) {
    assert.equal(isMetered(arn(route)), true, route);
  }
  assert.equal(isMetered(undefined), true);
});
test("quota exhaustion, concurrency, and rate throttling give distinct guidance", async () => {
  const response = body => ({ status: 429, json: async () => body });
  assert.match(await responseMessage(response({ type: "QUOTA_EXCEEDED" })), /shared API quota/);
  assert.match(await responseMessage(response({ message: "Limit Exceeded" })), /shared API quota/);
  assert.match(await responseMessage(response({ source: "team-concurrency" })), /Another investigation/);
  assert.equal(await responseMessage(response({ type: "THROTTLED" })), "Too many requests at once. Try again.");
  assert.match(await responseMessage({ status: 429, json: async () => { throw new Error(); } }), /service is limiting/);
  assert.equal(await responseMessage({ status: 403 }), "This account is not permitted to perform this action.");
});
