/** Public HTTPS host. Only the server constructs Portkey's provider configuration. */
import { timingSafeEqual } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:https";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { startGateway } from "./launcher.mjs";

const identifier = value => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const reject = (status, publicMessage) => { throw Object.assign(new Error(publicMessage), { status, publicMessage }); };
const same = (left, right) => {
  const a = Buffer.from(left || ""), b = Buffer.from(right || "");
  return a.length === b.length && timingSafeEqual(a, b);
};

export function hostedPolicy({ serviceToken, adminToken, region, routing, credentials, putEvidence }) {
  if (!serviceToken || serviceToken.length < 32 || !adminToken || adminToken.length < 32
      || serviceToken === adminToken) throw new Error("Distinct service and console secrets are required");
  return {
    adminToken,
    async prepare(request) {
      if (!same(request.headers.get("authorization"), `Bearer ${serviceToken}`)) reject(401, "Service authentication required");
      const allowed = new Set(["x-portkey-metadata", "x-portkey-trace-id"]);
      for (const [name] of request.headers) {
        if ((name.startsWith("x-portkey-") && !allowed.has(name))
            || name.startsWith("x-amz-")) reject(400, "Provider overrides are not accepted");
      }
      const text = await request.text();
      if (Buffer.byteLength(text) > 512 * 1024) reject(413, "Model request too large");
      let body, metadata;
      try {
        body = JSON.parse(text);
        metadata = JSON.parse(request.headers.get("x-portkey-metadata") || "{}");
      } catch { reject(400, "Invalid JSON"); }
      const callId = request.headers.get("x-portkey-trace-id");
      if (!identifier(callId) || !metadata || typeof metadata !== "object"
          || !identifier(metadata.request_id) || !identifier(metadata.team)
          || !identifier(metadata.principal)
          || !["devops-companion", "recovery-planner"].includes(metadata.agent_role)
          || !["true", "false"].includes(metadata.evidence_ready)
          || body?.model !== metadata.agent_role) reject(400, "Invalid model request identity");
      const bodyFields = new Set(["model", "input", "instructions", "tools", "tool_choice",
        "parallel_tool_calls", "max_output_tokens", "reasoning", "text", "store", "stream",
        "temperature", "top_p", "truncation", "metadata", "include"]);
      if (Object.keys(body).some(key => !bodyFields.has(key)) || body.stream === true) {
        reject(400, "Unsupported model request");
      }
      const timeout = Number(request.headers.get("x-module10-timeout-ms"));
      if (!Number.isInteger(timeout) || timeout < 100 || timeout > 25000) reject(400, "Invalid target timeout");
      const config = structuredClone(routing);
      const creds = await credentials();
      const configure = target => {
        target.request_timeout = timeout;
        if (target.provider === "bedrock") Object.assign(target, {
          aws_region: region, aws_access_key_id: creds.accessKeyId,
          aws_secret_access_key: creds.secretAccessKey, aws_session_token: creds.sessionToken || "",
        });
        for (const child of target.targets || []) configure(child);
      };
      configure(config);
      // Reconstruct an allowlist: neither browser sessions nor arbitrary inbound
      // headers can reach the provider or override SigV4.
      const headers = new Headers({
        "content-type": "application/json", "x-portkey-config": JSON.stringify(config),
        "x-portkey-trace-id": callId, "x-portkey-metadata": JSON.stringify(metadata),
      });
      return {
        request: new Request(request.url, { method: "POST", headers, body: text }),
        drill: request.headers.get("x-module10-drill") === "true",
      };
    },
    async persist(call, entry) {
      const key = `evidence/${call.metadata.team}/${call.metadata.principal}/${call.metadata.request_id}/${call.id}.json`;
      await putEvidence(key, JSON.stringify(entry));
      return key;
    },
  };
}

async function main() {
  const { defaultProvider } = await import("@aws-sdk/credential-provider-node");
  const { S3Client, PutObjectCommand } = await import("@aws-sdk/client-s3");
  const s3 = new S3Client({ maxAttempts: 2 });
  const policy = hostedPolicy({
    serviceToken: process.env.PORTKEY_SERVICE_TOKEN, adminToken: process.env.PORTKEY_CONSOLE_TOKEN,
    region: process.env.AWS_REGION,
    routing: JSON.parse(await readFile(new URL("./routing.json", import.meta.url), "utf8")),
    credentials: defaultProvider(),
    putEvidence: (Key, Body) => s3.send(new PutObjectCommand({
      Bucket: process.env.PORTKEY_EVIDENCE_BUCKET, Key, Body, ContentType: "application/json",
    }), { abortSignal: AbortSignal.timeout(3000) }),
  });
  const gateway = await startGateway(resolve(process.argv[2]), { hosted: {
    ...policy, port: 8443,
    tls: { createServer, serverOptions: {
      key: await readFile(process.env.PORTKEY_TLS_KEY),
      cert: await readFile(process.env.PORTKEY_TLS_CERT),
      maxHeaderSize: 32768,
    } },
  } });
  console.log(JSON.stringify({ ready: true }));
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, async () => {
    const timer = setTimeout(() => process.exit(1), 60000);
    timer.unref();
    await gateway.close({ graceful: true });
    process.exit(0);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(() => { console.error("Hosted gateway startup failed"); process.exitCode = 1; });
}
