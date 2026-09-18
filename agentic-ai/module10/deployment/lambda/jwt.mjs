import { createPublicKey, verify } from "node:crypto";

export async function verifyToken(token, { issuer, clientId, clientIds = [clientId], getKeys, now = Date.now() / 1000 }) {
  const parts = token.split(".");
  if (parts.length !== 3 || token.length > 8192) throw new Error("Unauthorized");
  const header = JSON.parse(Buffer.from(parts[0], "base64url"));
  const claims = JSON.parse(Buffer.from(parts[1], "base64url"));
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("Unauthorized");
  const jwks = await getKeys();
  const jwk = jwks.find((key) => key.kid === header.kid && key.kty === "RSA");
  if (!jwk || !verify("RSA-SHA256", Buffer.from(parts.slice(0, 2).join(".")),
      createPublicKey({ key: jwk, format: "jwk" }), Buffer.from(parts[2], "base64url"))) throw new Error("Unauthorized");
  if (claims.iss !== issuer || !clientIds.includes(claims.client_id) || claims.token_use !== "access"
      || !Number.isFinite(claims.exp) || claims.exp <= now
      || (claims.nbf !== undefined && claims.nbf > now)
      || typeof claims.sub !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(claims.sub)
      || typeof claims.scope !== "string" || !claims.scope.split(" ").includes("module10/invoke")) throw new Error("Unauthorized");
  return claims;
}
