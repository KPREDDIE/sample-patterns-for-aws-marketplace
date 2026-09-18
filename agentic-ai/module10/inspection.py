"""Read recorded transactions without creating an agent or model client."""
from .observability import read_session


def selected_records(session):
    rows = read_session(session)
    selected = []
    for section in (1, 2):
        candidates = [r for r in rows if r["demo.section"] == section]
        if not candidates:
            raise ValueError(f"No Section {section} capture. Rerun --section {section} --session {session} with telemetry enabled.")
        latest_run = candidates[-1]["demo.run.id"]
        selected.extend(r for r in candidates if r["demo.run.id"] == latest_run)
    if any(r.get("telemetry.export") != "collector-accepted" for r in selected):
        raise ValueError("This session has incomplete exports. Start the collector and rerun Sections 1–2 with MODULE10_OTLP_ENDPOINT set.")
    if any(r.get("telemetry.delivery") == "failed" for r in selected):
        raise ValueError("Logz.io delivery failed for this capture. Fix collector connectivity and rerun Sections 1–2.")
    completed = [r for r in selected if r["request.status"] == "completed"]
    platforms = [r for r in completed if r["demo.section"] == 1 and r["team.id"] == "platform" and r.get("recovery.plan_saved")]
    payments = [r for r in completed if r["demo.section"] == 1 and r["team.id"] == "payments" and not r.get("agent.planner_executed")]
    if not platforms or not payments or platforms[0]["feature_flag.version"] != payments[0]["feature_flag.version"]:
        raise ValueError("Section 1 needs platform and payments requests under the same enabled flag revision. Rerun Section 1.")
    normal = [r for r in completed if r["demo.section"] == 2 and not r["portkey.fallback_used"] and r.get("recovery.plan_saved")]
    fallback = [r for r in completed if r["demo.section"] == 2 and r["portkey.fallback_used"] and r.get("recovery.plan_saved")]
    if not normal or not fallback:
        raise ValueError("Section 2 needs both a completed normal plan and a completed fallback plan. Rerun Section 2.")
    return selected, (platforms[0], payments[0]), (normal[0], fallback[0])


def show(session, *, no_pause, clear, header, box, pause, concept=None,
         final_prompt="  Press Enter to finish this section..."):
    _, capability, routing = selected_records(session)
    planner, fallback = capability[0], routing[1]
    clear()
    header("SEE WHAT THE CALLER RECEIVED", "magenta")
    box("Understand the user's experience after the request",
        "Once an agent has answered, we need to understand what the user actually\n"
        "experienced. A successful response alone does not tell us which features\n"
        "were active or how the agent completed its work.\n\n"
        "We need to know whether the new Recovery Planner was available and\n"
        "actually used for a request, which model handled its work, and whether\n"
        "a failure caused it to fall back to another model.\n\n"
        "Logz.io correlates the request log and execution trace through a shared\n"
        "trace ID. That connects the caller, LaunchDarkly's feature decision,\n"
        "the agent's actions, and Portkey's model attempts in one request history.")
    pause("  Press Enter to inspect the capability decision...", no_pause)

    clear()
    header("EXPLAIN THE CAPABILITY DECISION", "magenta")
    box("The caller received the new Recovery Planner",
        "For this platform request, LaunchDarkly made the Recovery Planner\n"
        "available to the Companion. The trace connects that decision to the\n"
        "actual handoff, the planner's evidence checks, and the saved recovery\n"
        "draft. We can confirm that the caller received the new capability\n"
        "and see how it contributed to the response.")
    print(f"\n  Trace ID: {planner['trace_id']}\n")
    pause("  Press Enter to investigate the successful fallback...", no_pause)

    clear()
    header("EXPLAIN THE SUCCESSFUL FALLBACK", "magenta")
    box("A complete plan with a visible interruption",
        "In this planning request, GPT-5.6 Terra gathered evidence before its next\n"
        "model call received the simulated HTTP 429 rate-limit response. Portkey\n"
        "sent that turn to GPT-5.6 Luna with the evidence already collected,\n"
        "allowing the agent to finish the recovery draft. Logz.io connects the\n"
        "failed GPT-5.6 Terra attempt and successful GPT-5.6 Luna response to the\n"
        "same user request, explaining how the caller received a complete plan.")
    print(f"\n  Trace ID: {fallback['trace_id']}\n")
    takeaway = (
        "A successful response is only part of a user's experience. Correlating "
        "the caller, feature decision, agent actions, and model attempts lets us "
        "explain which capability ran and how the request completed."
    )
    if concept is None:
        # A running demo may load this module after its caller was edited.
        box("Module 10 Concept", takeaway)
    else:
        concept(takeaway)
    pause(final_prompt, no_pause)
