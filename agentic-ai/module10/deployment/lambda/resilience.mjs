import { randomUUID } from "node:crypto";

// Only trusted API Gateway authorizer context is accepted. The current field
// takes precedence over the legacy field during rolling deployments.
export function canManageControls(authorizer) {
  return (authorizer?.operator ?? authorizer?.presenter) === "true";
}

export function resilienceState(item) {
  return {
    enabled: item?.modelFaultEnabled?.BOOL === true,
    backend: "portkey",
    revision: item?.revision?.S || "initial",
  };
}

// Update only routing and the simulator; preserve session, section, and any
// request-specific CLI drill. CLI configuration writes also change revision.
export function resilienceUpdate(table, enabled, revision) {
  return {
    TableName: table, Key: { pk: { S: "DEMO" }, sk: { S: "CONFIG" } },
    UpdateExpression: "SET modelFaultEnabled = :enabled, backend = :backend, revision = :next",
    ConditionExpression: revision === "initial" ? "attribute_not_exists(revision)" : "revision = :expected",
    ExpressionAttributeValues: {
      ":enabled": { BOOL: enabled }, ":backend": { S: "portkey" }, ":next": { S: randomUUID() },
      ...(revision === "initial" ? {} : { ":expected": { S: revision } }),
    },
    ReturnValues: "ALL_NEW",
  };
}

export function sharedFaultEnabled(item, authorizer) {
  return canManageControls(authorizer)
    && item?.modelFaultEnabled?.BOOL === true;
}

export async function resilience(event, read, write) {
  const canEdit = canManageControls(event.requestContext?.authorizer);
  const result = (statusCode, body) => ({ statusCode, body });
  if (!["GET", "POST"].includes(event.httpMethod)) return result(405, { error: "Method not allowed" });
  if (event.httpMethod === "POST" && !canEdit) return result(403, { error: "Operator permission required" });
  let body;
  if (event.httpMethod === "POST") {
    try { body = JSON.parse(event.body); } catch { return result(400, { error: "Invalid JSON" }); }
    if (!body || Array.isArray(body) || Object.keys(body).some(k => !["enabled", "revision"].includes(k))
        || typeof body.enabled !== "boolean" || typeof body.revision !== "string"
        || !body.revision.length || body.revision.length > 128)
      return result(400, { error: "Expected enabled and the last observed revision" });
  }
  const current = await read();
  if (!body) return result(200, { ...current, can_edit: canEdit });
  if (body.revision !== current.revision) return result(409, { error: "Simulator changed. Refresh before trying again." });
  if (body.enabled === current.enabled && current.backend === "portkey")
    return result(200, { ...current, can_edit: canEdit });
  try {
    return result(200, { ...await write(body.enabled, body.revision), can_edit: canEdit });
  } catch (error) {
    if (error.name === "ConditionalCheckFailedException")
      return result(409, { error: "Simulator changed. Refresh before trying again." });
    throw error;
  }
}
