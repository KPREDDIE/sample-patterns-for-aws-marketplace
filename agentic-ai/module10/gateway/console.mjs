/** Portkey's bundled console, with a bounded feed of observed gateway calls. */
import { randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export async function installConsole(app, source, { adminToken, hosted = false } = {}) {
  // Share the JSON module used by Portkey's auth handlers without editing its
  // pinned checkout or persisting a console credential.
  const { default: conf } = await import(pathToFileURL(join(source, "conf.json")));
  const token = adminToken || randomBytes(24).toString("hex");
  conf.admin_token = token;
  const auth = await import(pathToFileURL(join(source, "src/middlewares/adminAuth/index.ts")));
  let html = await readFile(join(source, "src/public/index.html"), "utf8");
  const script = await readFile(new URL("./console-client.js", import.meta.url), "utf8");

  // The log inspector is self-contained. No remote scripts receive access to
  // its session or model content; the native layout and auth flow are retained.
  html = html
    .replace(/<!-- Google Tag Manager[\s\S]*?<!-- End Google Tag Manager[^>]*-->/g, "")
    .replace(/<script\b[^>]*\bsrc="https:[^"]*"[^>]*>[\s\S]*?<\/script>/g, "")
    .replace(/<link\b[^>]*href="https:[^"]*"[^>]*>/g, "")
    .replace(/<img\b[^>]*src="https:[^"]*"[^>]*>/g, '<strong>Portkey AI Gateway</strong>')
    .replace(/<p class="admin-auth-subtitle">[\s\S]*?<\/p>/,
      `<p class="admin-auth-subtitle">${hosted ? "Sign in with the console credential printed by the console-login command." : "Open the console link printed by this demo to sign in for this run."}</p>`)
    .replace("      ensureAdminAuth();", "      ensureDemoConsoleAuth();")
    .replace("</head>", '<link rel="icon" href="data:,"><script src="/public/module10.js"></script></head>');

  app.use("/public/*", async (c, next) => {
    c.header("Cache-Control", "no-store");
    c.header("Referrer-Policy", "no-referrer");
    c.header("Content-Security-Policy",
      "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'");
    await next();
  });
  app.get("/public", (c) => c.redirect("/public/logs"));
  app.get("/public/", (c) => c.redirect("/public/logs"));
  app.get("/public/logs", (c) => c.html(html));
  app.get("/public/module10.js", (c) => c.body(script, 200, { "Content-Type": "text/javascript" }));
  app.post("/public/auth", async (c) => {
    const result = await auth.adminAuthLoginHandler(c);
    if (hosted && c.res.headers.has("set-cookie")) {
      c.header("Set-Cookie", c.res.headers.get("set-cookie") + "; Secure");
    }
    return result;
  });
  app.get("/public/auth/session", auth.adminAuthSessionStatusHandler);

  const encoder = new TextEncoder();
  const history = [];
  const entries = [];
  const clients = new Set();
  let sequence = 0;
  const encode = (event, data, id = "") => encoder.encode(
    `${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
  );
  app.get("/log/stream", auth.adminAuthMiddleware, (c) => {
    let client;
    const body = new ReadableStream({
      start(controller) {
        const close = () => {
          clearInterval(client.timer);
          clients.delete(client);
          try { controller.close(); } catch { /* Already disconnected. */ }
        };
        client = {
          close,
          send(frame) {
            if (controller.desiredSize <= 0) return close();
            try { controller.enqueue(frame); } catch { close(); }
          },
        };
        client.timer = setInterval(() => client.send(encode("heartbeat", {})), 15000);
        clients.add(client);
        client.send(encode("connected", {}));
        for (const frame of history) client.send(frame);
      },
      cancel() { client.close(); },
    }, { highWaterMark: 102 });
    return new Response(body, { headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
    } });
  });

  const bounded = (value) => {
    const text = JSON.stringify(value);
    return text.length <= 50000 ? value : { truncated: true, preview: text.slice(0, 50000) };
  };
  const publish = (entry) => {
    const frame = encode("log", entry, String(++sequence));
    history.push(frame);
    entries.push(entry);
    if (history.length > 100) history.shift();
    if (entries.length > 100) entries.shift();
    for (const client of clients) client.send(frame);
  };
  return {
    token,
    snapshot() { return entries; },
    replay(records) {
      if (!Array.isArray(records) || records.length > 100
          || records.some((entry) => !entry?.requestOptions?.[0]?.module10?.call_id)) {
        throw new Error("Invalid console evidence");
      }
      for (const entry of records) publish(entry);
    },
    async record(call, request, response, duration) {
      let output;
      try { output = await response.clone().json(); } catch { output = { error: "Response was not JSON." }; }
      const entry = {
        time: new Date().toLocaleTimeString(), method: "POST",
        endpoint: "/openai/v1/responses", status: response.status,
        duration: Math.round(duration),
        requestOptions: [{
          requestParams: bounded(request), response: bounded(output),
          module10: {
            call_id: call.id, agent: call.metadata.agent_role, attempts: call.attempts,
            trace_id: call.metadata.trace_id, request_id: call.metadata.request_id,
          },
        }],
      };
      publish(entry);
      return entry;
    },
    close() { for (const client of clients) client.close(); },
  };
}
