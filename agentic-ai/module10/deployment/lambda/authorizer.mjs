import { verifyToken } from "./jwt.mjs";
import { isMetered } from "./quota.mjs";
export { verifyToken } from "./jwt.mjs";
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";

const database = new DynamoDBClient({});
let keys;
let keysUntil = 0;

async function getKeys() {
  if (!keys || Date.now() >= keysUntil) {
    const response = await fetch(`${process.env.ISSUER}/.well-known/jwks.json`, { signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error("Unauthorized");
    keys = (await response.json()).keys;
    keysUntil = Date.now() + 300_000;
  }
  return keys;
}

export async function handler(event) {
  let claims;
  try {
    const token = event.authorizationToken?.match(/^Bearer ([^\s]+)$/)?.[1];
    if (!token) throw new Error("Unauthorized");
    claims = await verifyToken(token, { issuer: process.env.ISSUER,
      clientIds: JSON.parse(process.env.CLIENT_IDS || JSON.stringify([process.env.CLIENT_ID])), getKeys });
  } catch {
    keysUntil = 0;
    throw new Error("Unauthorized");
  }
  const { Item } = await database.send(new GetItemCommand({
    TableName: process.env.TABLE_NAME, Key: { pk: { S: `USER#${claims.sub}` }, sk: { S: "PROFILE" } },
    ConsistentRead: true,
  }));
  const team = Item?.team?.S;
  const usageKeys = JSON.parse(process.env.USAGE_KEYS);
  const allowed = Item?.canInvoke?.BOOL === true && Object.hasOwn(usageKeys, team);
  return {
    principalId: claims.sub,
    policyDocument: { Version: "2012-10-17", Statement: [
      { Action: "execute-api:Invoke", Effect: allowed ? "Allow" : "Deny", Resource: event.methodArn },
    ] },
    context: { team: allowed ? team : "denied", operator: Item?.operator?.BOOL === true ? "true" : "false" },
    ...(allowed && isMetered(event.methodArn) ? { usageIdentifierKey: usageKeys[team] } : {}),
  };
}
