const known = value => value === undefined || value === null ? "Unavailable" : String(value);
const enabled = value => typeof value === "boolean" ? (value ? "Enabled" : "Disabled") : "Unavailable";

/** Only the response snapshot supplies feature, execution, and model evidence. */
export function detailSections(body) {
  const calls = Array.isArray(body.model_calls) ? body.model_calls : [];
  const events = Array.isArray(body.events) ? body.events : [];
  const planner = Array.isArray(body.events)
    ? [...events, ...calls].some(e => ["Recovery Planner", "recovery-planner"].includes(e.agent)) : null;
  return [
    { title: "Request", rows: [
      ["Request ID", body.request_id], ["Team", body.team], ["Outcome", body.status],
    ] },
    { title: "LaunchDarkly capabilities", rows: [
      ["Investigation", enabled(body.capabilities?.investigation)],
      ["Recovery planning", enabled(body.capabilities?.recovery_planning)],
      ["Flag", body.release?.flag_key], ["Revision", body.release?.version],
      ["Reason", body.release?.reason?.kind],
      ["Planner executed", planner === null ? null : planner ? "Yes" : "No"],
    ], note: "Evaluation for this request. LaunchDarkly SDK with shared S3 file configuration." },
    { title: "Agent activity", rows: events.length
      ? events.map(e => [e.tool || "Activity", `${known(e.agent)} · ${known(e.status)}`])
      : [["Tools", Array.isArray(body.events) ? "None recorded" : null]] },
    { title: "Models & routing", rows: [
      ["Gateway", body.routing ? "Portkey" : calls.length ? "Direct Bedrock" : null],
      ["Fallback", body.routing ? (body.routing.fallback_used ? "Used" : "Not used") : calls.length ? "Not applicable" : null],
      ...calls.flatMap((call, index) => [
        [`${index + 1}. ${known(call.agent)}`, `Requested: ${known(call.requested_model)} → Served: ${known(call.model)}`],
        ...(call.attempts || []).map((attempt, j) => [
          `Attempt ${j + 1}`, `${known(attempt.model)} · HTTP ${known(attempt.status_code)}${attempt.source === "injected" ? " · SIMULATED" : ""}`,
          ...(Number.isInteger(attempt.status_code) && attempt.status_code >= 400 && attempt.status_code <= 599 ? ["attempt-error"] : []),
        ]),
      ]),
    ] },
    { title: "Reported usage", rows: [
      ["Input tokens", body.usage?.input_tokens], ["Output tokens", body.usage?.output_tokens],
      ["Total tokens", body.usage?.total_tokens],
    ], note: "Usage from completed model calls. Unavailable usage is not estimated." },
  ].map(section => ({ ...section, rows: section.rows.map(([label, value, ...style]) => [label, known(value), ...style]) }));
}
