"""Browser actions with manually paced terminal commentary; no agent invocations."""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import re
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def validate_url(value):
    if not isinstance(value, str):
        raise ValueError("--with-ui requires application outputs with a configured HTTPS uiUrl.")
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("--with-ui requires application outputs with a configured HTTPS uiUrl.")


def page(ui, title, body, color="cyan"):
    ui.clear_screen()
    ui.header(title, color)
    ui.box("Browser and terminal", body, color=color)


def advance(context, ui, text):
    ui.pause(f"  {text} Press Enter to continue the commentary...", context.no_pause)


def capabilities(context, ui):
    ui.clear_screen()
    ui.header("SECTION 2 — CAN WE SAFELY GIVE AN AGENT MORE CAPABILITY?", "green")
    ui.box("Background",
        "The DevOps Companion is running and investigating deployment\n"
        "problems. A new Recovery Planner can check service dependencies and\n"
        "rollback prerequisites, then prepare a plan for the release owner.\n\n"
        "We need to control which callers receive the new planner and be able\n"
        "to withdraw it while investigation stays available.")
    ui.box("Release the planner safely",
        "LaunchDarkly provides runtime control: target changes to specific users\n"
        "or teams and switch capabilities on or off without redeploying.\n"
        "Here, a feature flag decides whether the Companion can delegate to\n"
        "the Recovery Planner for the caller's team.")
    ui.pause("  Press Enter to continue the release engineering script...", context.no_pause)

    ui.clear_screen()
    ui.header("MAKE RECOVERY PLANNING AVAILABLE", "green")
    ui.box("What LaunchDarkly controls",
        "Recovery planning OFF → investigate and hand planning to a human\n"
        "Recovery planning ON  → platform can delegate to the Recovery Planner\n"
        "                        other teams keep investigation only\n\n"
        "The application uses the flag to expose or remove the planner handoff.\n"
        "The endpoint stays the same, and no restart is needed.")
    ui.box("Limit the release to one team",
        "Platform receives the new capability while payments stays outside the pilot.\n"
        "Turning planning off removes the handoff while investigation stays available.\n\n"
        "The agents run on AgentCore and use Amazon Bedrock to investigate a\n"
        "fictional deployment incident. Recovery plans are drafts for an operator\n"
        "to review; the agents do not carry out the proposed changes.")
    ui.concept(
        "A feature release changes what an agent can do for a particular caller. "
        "Targeting lets one team use the Recovery Planner while other teams keep "
        "their existing behavior through the same endpoint.")
    ui.pause("  Press Enter to explore model routing..." if context.full_demo
             else "  Press Enter to finish this section...", context.no_pause)


def routing(context, ui):
    ui.clear_screen()
    ui.header("SECTION 3 — KEEP THE RECOVERY PLANNER AVAILABLE", "cyan")
    ui.box("Keep recovery planning available",
        "Model inference can be interrupted when a preferred model is rate limited\n"
        "or temporarily unavailable. The Recovery Planner needs a fallback that can\n"
        "continue its work without discarding evidence it has already collected.\n\n"
        "Frontier model providers offer variants within a model family. A variant\n"
        "that has been validated against the same task requirements can provide an\n"
        "acceptable fallback when the preferred model cannot serve a request.\n\n"
        "For this demo, the Recovery Planner uses OpenAI GPT-5.6 Terra as its\n"
        "preferred model. GPT-5.6 Luna has been validated as an acceptable fallback\n"
        "if Terra is rate limited or otherwise unavailable.")
    ui.box("How Portkey AI helps",
        "Portkey sends Recovery Planner requests to GPT-5.6 Terra. If that route\n"
        "is rate limited or unavailable, Portkey sends the interrupted turn to\n"
        "the validated GPT-5.6 Luna fallback with the planner's evidence intact.")
    ui.pause("  Press Enter to explain model interruptions...", context.no_pause)

    ui.clear_screen()
    ui.header("MODEL INTERRUPTIONS AND FALLBACK", "yellow")
    ui.box("A rate limit can interrupt a model request",
        "The demo's failure drill simulates HTTP 429 on the planner's next\n"
        "GPT-5.6 Terra request after it gathers evidence.\n\n"
        "The interruption is injected inside the demo gateway. Portkey handles\n"
        "it using the configured fallback policy; successful model calls are real.",
        color="yellow")
    ui.box("Continue with the evidence already collected",
        "When that model request fails, Portkey forwards the turn to GPT-5.6 Luna\n"
        "with the planner's existing tool results. The planner can continue\n"
        "preparing a recovery draft without starting the investigation over.")
    ui.pause("  Press Enter to explain fallback quality...", context.no_pause)

    ui.clear_screen()
    ui.header("KEEP THE RECOVERY PLAN USEFUL", "cyan")
    ui.box("A validated fallback for the same task",
        "GPT-5.6 Luna was validated against the Recovery Planner's requirements\n"
        "for tool use, incident reasoning, and recovery-plan quality. This gives\n"
        "the planner an acceptable fallback when GPT-5.6 Terra is unavailable.")
    ui.box("Make the routing decision visible",
        "Model attempts record which model served each turn and whether fallback\n"
        "occurred. The response includes an explicit fallback indicator.\n"
        "The release owner still needs to review and approve the proposed recovery.")
    ui.concept(
        "The Recovery Planner prefers GPT-5.6 Terra and uses the validated GPT-5.6 "
        "Luna fallback to continue with existing evidence when Terra is unavailable.")
    ui.pause("  Press Enter to understand the caller's experience..." if context.full_demo
             else "  Press Enter to finish this section...", context.no_pause)


def select_evidence(records):
    """Latest matching completed requests, independent of demo session or section."""
    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("request.status") != "completed":
            continue
        if not re.fullmatch(r"[0-9a-f]{32}", str(record.get("trace_id", ""))):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(record.get("request.id", ""))):
            continue
        try:
            timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        candidates.append((timestamp, record))
    candidates.sort(key=lambda pair: (pair[0], pair[1]["trace_id"]))
    result = {}
    for _, record in candidates:
        planned = (record.get("feature_flag.result.value") is True
                   and record.get("feature_flag.provider.name") == "LaunchDarkly"
                   and record.get("agent.planner_executed") is True
                   and record.get("recovery.plan_saved") is True)
        if planned:
            result["planning_on"] = record
        if record.get("portkey.fallback_used") is True:
            result["fallback"] = record
    return result


def latest_evidence(outputs):
    """Read existing metadata using terminal AWS credentials; never configure the demo."""
    bucket = outputs["artifactBucket"]
    region = outputs["runtimeArn"].split(":")[3]
    s3 = boto3.client("s3", region_name=region)
    keys = (item["Key"]
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="manifests/")
            for item in page.get("Contents", []) if item["Key"].endswith(".json"))

    def read(key):
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            with response["Body"] as stream:
                return json.load(stream)
        except (ValueError, UnicodeDecodeError):
            return None
        except ClientError as error:
            if error.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    with ThreadPoolExecutor(max_workers=8) as pool:
        return select_evidence(pool.map(read, keys))


def read_evidence(context, ui):
    try:
        evidence = latest_evidence(context.client.outputs)
        context.browser_evidence = evidence
        return evidence
    except (OSError, KeyError, IndexError, ValueError, RuntimeError, BotoCoreError, ClientError):
        ui.box("Capture lookup unavailable",
            "Could not read trace metadata from S3 with the terminal's AWS credentials.\n"
            "Continue using the browser's request details.\n"
            "No investigation was started.")
        return {}


EXAMPLES = {
    "planning_on": ("Recovery planner enabled", "LaunchDarkly enabled planning; the planner ran and saved a recovery draft."),
    "fallback": ("Model fallback", "The request completed with recorded Portkey fallback."),
}


def show_example(ui, key, record):
    title, explanation = EXAMPLES[key]
    if record is None:
        ui.box(title, "No matching completed request was found in the deployment's trace metadata.\n"
               "You can continue the narrative and use the browser's request details.")
        return
    export = record.get("telemetry.export")
    telemetry = ("The collector accepted the telemetry export; confirm ingestion in Logz.io."
                 if export == "collector-accepted" else
                 "Telemetry export is incomplete or unavailable; this trace may not be in Logz.io.")
    if record.get("telemetry.delivery") == "failed":
        telemetry = "Telemetry delivery failed; this trace may not be in Logz.io."
    ui.box(title, f"{explanation}\n\n"
           f"Request ID: {record['request.id']}\nTrace ID: {record['trace_id']}\n"
           f"Completed: {record['timestamp']}\n"
           f"Logz.io search: trace_id:\"{record['trace_id']}\"\n\n{telemetry}")


def inspection(context, ui):
    context.browser_evidence = {}
    page(ui, "SECTION 4 — EXPLAIN THE CALLER'S EXPERIENCE",
        "Request details connect the capability decision, agent actions, and model attempts.\n"
        "Logz.io can correlate the completion log and trace using the same trace ID.\n\n"
        "Open a browser result's Request details, copy its trace search, and open Logz.io.\n"
        "These are the latest matching completed requests across this deployment.\n"
        "Recorded trace IDs do not by themselves confirm Logz.io ingestion.")
    evidence = read_evidence(context, ui)
    ui.pause("  Press Enter to review the recorded examples...", context.no_pause)
    ui.clear_screen()
    for key in ("planning_on", "fallback"):
        show_example(ui, key, evidence.get(key))
    ui.concept("Use recorded evidence to explain what ran. Missing examples remain unverified.")
    advance(context, ui, "Continue to the module wrap-up." if context.full_demo else "Finish this section.")


def wrap_up(context, ui):
    ui.clear_screen()
    ui.header("MODULE 10 COMPLETE", "bold green")
    ui.box("What we covered",
        "• Protect the agent exposure boundary\n"
        "  Amazon API Gateway applies request controls, Amazon Cognito establishes\n"
        "  caller identity, and an AWS Lambda adapter securely invokes the agents\n"
        "  hosted on Amazon Bedrock AgentCore Runtime.\n\n"
        "• Release a capability to selected callers\n"
        "  LaunchDarkly enables the Recovery Planner for the platform team while\n"
        "  the payments team keeps the existing investigation-only experience.\n\n"
        "• Keep recovery planning available\n"
        "  Portkey AI routes the Recovery Planner to OpenAI GPT-5.6 Terra and moves\n"
        "  an interrupted turn to the validated GPT-5.6 Luna fallback without\n"
        "  discarding the evidence already collected.\n\n"
        "• Explain what each caller experienced\n"
        "  Logz.io connects the API request, LaunchDarkly decision, agent actions,\n"
        "  model attempts, and fallback outcome in one trace.")
    observed = getattr(context, "browser_evidence", {})
    lines = [f"{EXAMPLES[key][0]}: {'matching trace found' if key in observed else 'no matching trace found'}."
             for key in ("planning_on", "fallback")]
    ui.box("Recorded browser outcomes", "\n".join(lines)
           + "\nConfirm telemetry ingestion in Logz.io using the displayed trace IDs.")
    ui.pause("  Press Enter to finish...", context.no_pause)
