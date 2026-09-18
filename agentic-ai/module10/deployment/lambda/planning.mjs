// Storage is injected so authorization and concurrent updates can be tested
// without AWS. This endpoint changes only the shared flag's on/off state.
import { canManageControls } from "./resilience.mjs";

export async function planning(event, read, write) {
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
  const { document, revision } = await read();
  const flag = document.flags["module10-recovery-planning"];
  if (typeof flag.on !== "boolean" || !Number.isSafeInteger(flag.version))
    throw new Error("Invalid flag configuration");
  const state = () => ({ enabled: flag.on, revision, can_edit: canEdit });
  if (!body) return result(200, state());
  if (body.revision !== revision) return result(409, { error: "Flag changed. Refresh before trying again." });
  if (flag.on === body.enabled) return result(200, state());
  flag.on = body.enabled;
  flag.version += 1;
  try {
    const updatedRevision = await write(document, revision);
    return result(200, { ...state(), revision: updatedRevision });
  } catch (error) {
    if (["PreconditionFailed", "ConditionalRequestConflict"].includes(error.name))
      return result(409, { error: "Flag changed. Refresh before trying again." });
    throw error;
  }
}
