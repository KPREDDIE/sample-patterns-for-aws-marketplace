// Test-only upstream substitution. Routing, SigV4, fallback and timeouts still
// execute inside the real, pinned Portkey gateway.
import { startGateway } from "../module10/gateway/launcher.mjs";
import { hostedPolicy } from "../module10/gateway/hosted.mjs";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";

const transport = globalThis.fetch;
const upstream = process.env.MODULE10_TEST_UPSTREAM;
if (!upstream?.startsWith("http://127.0.0.1:")) throw new Error("A loopback test upstream is required");
const gateway = await startGateway(process.argv[2], {
  ...(process.env.MODULE10_TEST_EVIDENCE ? { hosted: {
    ...hostedPolicy({
      serviceToken: "s".repeat(48), adminToken: "p".repeat(48), region: "us-test-1",
      routing: JSON.parse(await readFile(new URL("../module10/gateway/routing.json", import.meta.url))),
      credentials: async () => ({ accessKeyId: "FAKE_TEST_ACCESS", secretAccessKey: "FAKE_TEST_SECRET", sessionToken: "FAKE_TEST_TOKEN" }),
      putEvidence: async (key, value) => {
        const path = join(process.env.MODULE10_TEST_EVIDENCE, key);
        await mkdir(dirname(path), { recursive: true });
        await writeFile(path, value);
      },
    }), port: 0,
  } } : {}),
  upstreamFetch: (url, options) => transport(upstream, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      url,
      headers: Object.fromEntries(new Headers(options.headers)),
      payload: JSON.parse(options.body),
    }),
    signal: options.signal,
  }),
});
console.log(JSON.stringify({ ready: true, base_url: gateway.baseUrl, console_url: gateway.consoleUrl }));
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.once(signal, () => { gateway.close(); process.exit(0); });
}
