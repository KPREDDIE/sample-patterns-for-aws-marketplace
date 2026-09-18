/**
 * Loopback host for the real Portkey Hono application.
 * Portkey performs routing, signing and fallback. This host adds a bounded
 * failure drill and records actual outbound attempts without logging credentials.
 */
import { AsyncLocalStorage } from "node:async_hooks";
import { createRequire } from "node:module";
import { resolve, join } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { installConsole } from "./console.mjs";

const calls = new AsyncLocalStorage();

export async function startGateway(source, { upstreamFetch = globalThis.fetch, hosted } = {}) {
  const require = createRequire(join(source, "package.json"));
  const { serve } = await import(pathToFileURL(require.resolve("@hono/node-server")));
  const { default: gateway } = await import(pathToFileURL(join(source, "src/index.ts")));
  const { LogsService } = await import(pathToFileURL(join(source, "src/handlers/services/logsService.ts")));
  const originalAddRequestLog = LogsService.prototype.addRequestLog;
  // Portkey records the actual selected config index after each attempt,
  // including unsuccessful targets. Copy only that field, never its headers.
  LogsService.prototype.addRequestLog = function (log) {
    const attempt = calls.getStore()?.attempts.at(-1);
    if (attempt && log.lastUsedOptionIndex !== undefined) {
      attempt.gateway_target = String(log.lastUsedOptionIndex);
    }
    return originalAddRequestLog.call(this, log);
  };
  const consoleFeed = await installConsole(gateway, source, {
    adminToken: hosted?.adminToken, hosted: Boolean(hosted),
  });
  let drill = { armed: false, consumed: false };
  const originalFetch = globalThis.fetch;

  // Only requests made by this invocation of Portkey enter the hook. Tool
  // execution stays in Python; no agent workflow or fallback logic lives here.
  globalThis.fetch = async (url, options) => {
    const call = calls.getStore();
    if (!call) return originalFetch(url, options);
    const endpoint = new URL(url);
    if (!/^bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com$/.test(endpoint.hostname)
        || endpoint.pathname !== "/openai/v1/responses" || endpoint.protocol !== "https:") {
      throw new Error("The demo only permits native Bedrock Runtime Responses requests");
    }
    const payload = JSON.parse(options.body);
    const attempt = {
      model: payload.model,
      source: "bedrock",
      status_code: null,
      duration_ms: 0,
      start_time_unix_ms: Date.now(),
    };
    call.attempts.push(attempt);
    const start = performance.now();
    let response;
    try {
      const activeDrill = hosted ? call.drill : drill;
      if (activeDrill.armed && !activeDrill.consumed
          && call.metadata.agent_role === "recovery-planner"
          && call.metadata.evidence_ready === "true"
          && payload.model.endsWith(".gpt-5.6-terra")) {
        activeDrill.consumed = true;
        attempt.source = "injected";
        response = Response.json({
          error: { type: "demo_throttle", message: "Locally injected GPT-5.6 Terra 429; no AWS request was made." },
        }, { status: 429 });
      } else {
        response = await upstreamFetch(url, options);
      }
      if (response.ok) {
        try {
          // Keep body reading inside the target timeout, including a response
          // whose headers arrive promptly but whose body stalls.
          const body = await response.clone().json();
          attempt.served_model = body?.model ?? null;
          attempt.response_id = body?.id ?? null;
        } catch (error) {
          // Invalid model output is not a transport failure or a fallback trigger.
          if (!(error instanceof SyntaxError)) throw error;
        }
      }
    } catch (error) {
      // The gateway receives an HTTP outcome and applies its configured policy.
      // Transport errors carry no fabricated AWS status or provider response.
      const timedOut = options.signal?.aborted || error.name === "AbortError";
      if (response) attempt.upstream_status_code = response.status;
      attempt.source = timedOut ? "gateway_timeout" : "transport_error";
      response = Response.json({
        error: { type: attempt.source, message: timedOut ? "Model request timed out." : "Model transport failed." },
      }, { status: timedOut ? 408 : 503 });
    }
    attempt.status_code = response.status;
    attempt.duration_ms = Math.round(performance.now() - start);
    attempt.end_time_unix_ms = Date.now();
    return response;
  };

  const server = serve({
    hostname: hosted ? "0.0.0.0" : "127.0.0.1",
    port: hosted?.port ?? 0,
    ...(hosted?.tls || {}),
    fetch: async (request) => {
      const path = new URL(request.url).pathname;
      if (path === "/public" || path.startsWith("/public/") || path === "/log/stream") {
        return gateway.fetch(request);
      }
      if (path === "/ping" && request.method === "GET") {
        return Response.json({ service: "module10-portkey", status: "ok" });
      }
      if (hosted && path.startsWith("/__demo/")) {
        return Response.json({ error: "not found" }, { status: 404 });
      }
      if (path === "/__demo/console-evidence") {
        if (request.headers.get("x-module10-console-token") !== consoleFeed.token) {
          return Response.json({ error: "Forbidden" }, { status: 403 });
        }
        if (request.method === "GET") return Response.json(consoleFeed.snapshot());
        if (request.method === "POST") {
          const text = await request.text();
          if (text.length > 2_000_000) return Response.json({ error: "Evidence too large" }, { status: 413 });
          try { consoleFeed.replay(JSON.parse(text)); }
          catch { return Response.json({ error: "Invalid evidence" }, { status: 400 }); }
          return Response.json({ status: "loaded" });
        }
      }
      if (path === "/__demo/drill" && request.method === "POST") {
        const body = await request.json();
        if (typeof body.armed !== "boolean") return Response.json({ error: "armed must be boolean" }, { status: 400 });
        drill = { armed: body.armed, consumed: false };
        return Response.json(drill);
      }
      if (path === "/__demo/drill" && request.method === "GET") return Response.json(drill);
      if (path !== "/v1/openai/v1/responses" || request.method !== "POST") {
        return Response.json({ error: "not found" }, { status: 404 });
      }
      let prepared;
      try {
        if (hosted) prepared = await hosted.prepare(request);
      } catch (error) {
        return Response.json({ error: error.publicMessage || "Gateway request rejected" },
          { status: error.status || 503 });
      }
      if (prepared) request = prepared.request;
      const metadata = JSON.parse(request.headers.get("x-portkey-metadata") || "{}");
      const call = { id: request.headers.get("x-portkey-trace-id"), metadata, attempts: [],
        drill: { armed: prepared?.drill === true, consumed: false } };
      const body = await request.clone().json();
      const started = performance.now();
      // The native proxy forwards incoming headers. These belong to the local
      // hop and must not overwrite the Host and Authorization signed by Portkey.
      const headers = new Headers(request.headers);
      for (const key of ["host", "authorization", "content-length", "cookie"]) headers.delete(key);
      const forwarded = new Request(request, { headers });
      return calls.run(call, async () => {
        const result = await gateway.fetch(forwarded);
        const entry = await consoleFeed.record(call, body, result, performance.now() - started);
        const resultHeaders = new Headers(result.headers);
        resultHeaders.set("x-module10-attempts", JSON.stringify(call.attempts));
        if (hosted) {
          try {
            resultHeaders.set("x-module10-evidence-key", await hosted.persist(call, entry));
          } catch {
            return Response.json({ error: "Gateway evidence persistence failed" }, {
              status: 503, headers: resultHeaders,
            });
          }
        }
        return new Response(result.body, { status: result.status, headers: resultHeaders });
      });
    },
  });
  await new Promise((done) => server.listening ? done() : server.once("listening", done));
  const address = server.address();
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    consoleUrl: `http://127.0.0.1:${address.port}/public/logs#token=${consoleFeed.token}`,
    close: ({ graceful = false } = {}) => {
      consoleFeed.close();
      return new Promise((done) => {
        server.close(() => {
          globalThis.fetch = originalFetch;
          LogsService.prototype.addRequestLog = originalAddRequestLog;
          done();
        });
        if (!graceful) server.closeAllConnections();
        else server.closeIdleConnections();
      });
    },
  };
}

async function main() {
  const source = resolve(process.argv[2]);
  const gateway = await startGateway(source);
  console.log(JSON.stringify({ ready: true, base_url: gateway.baseUrl, console_url: gateway.consoleUrl }));
  for (const signal of ["SIGTERM", "SIGINT"]) {
    process.once(signal, () => { gateway.close(); process.exit(0); });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
