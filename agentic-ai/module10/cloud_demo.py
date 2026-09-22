"""Cloud lab adapter; reuses the local capability and routing exercises."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import re
from types import SimpleNamespace
from uuid import uuid4

from .cloud_client import CloudClient


class CloudGateway:
    """Open the actual ECS console; no gateway process runs on the local machine."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.drill_armed = False

    def set_drill(self, armed):
        self.drill_armed = armed

    @property
    def console_url(self):
        import boto3
        from .portkey_remote import discovery_context
        value = json.loads(boto3.client("ssm").get_parameter(
            Name=self.outputs["portkeyDiscoveryParameter"])["Parameter"]["Value"])
        url, _ = discovery_context(value)
        return url + "/public/logs"

    def display(self, response):
        pass  # The hosted console observes the original gateway requests.


class CloudContext:
    def __init__(self, args, client, cleanup):
        self.client = client
        self.session = args.session or "module10-" + uuid4().hex[:12]
        self.no_pause = args.no_pause
        self.with_ui = getattr(args, "with_ui", False)
        self.change_summary = args.change_summary
        self.full_demo = args.section is None
        self.base_url = client.base_url
        self.last_response = None
        self.section = 0
        self.backend = "portkey"
        self.gateway = CloudGateway(client.outputs)
        self.runtime = SimpleNamespace(mode="cloud", model_gateway=SimpleNamespace(gateway=self.gateway))

    def select(self, section):
        # Preserve the recorded scenario identifiers consumed by inspection.
        self.section = section
        self.backend = "portkey"
        if not self.with_ui:
            self.client.configure(self.session, self.section, self.backend)

    def set_recovery_planning_enabled(self, enabled):
        self.client.configure(self.session, self.section, self.backend, planning_enabled=enabled)

    def request(self, team):
        request_id = uuid4().hex
        if self.gateway.drill_armed:
            self.client.configure(self.session, self.section, self.backend, drill_request=request_id)
            self.gateway.drill_armed = False
        response = self.client.invoke(team, self.change_summary, request_id=request_id)
        self.gateway.display(response)
        self.last_response = response
        return response


def expect(response, status, name):
    if response.status_code != status:
        raise RuntimeError(f"{name}: expected HTTP {status}, received {response.status_code}: {response.body}")
    return response


def exposure(context, ui):
    client = context.client
    ui.clear_screen()
    ui.header("SECTION 1 — API GATEWAY: EXPOSE THE AGENT", "cyan")
    ui.box("A service boundary for external callers",
        "Exposing an agent requires control over who can invoke it and how often.\n\n"
        "Browser or terminal → API Gateway REST API → Lambda adapter → AgentCore Runtime\n\n"
        "API Gateway enforces authentication, authorization, and team rate limits.\n"
        "Runtime policies allow only the adapter to invoke the agent, preventing\n"
        "clients from bypassing these controls.")
    ui.box("Cognito sign-in and access checks",
        "Cognito signs users in and issues an access token.\n"
        "A Lambda authorizer validates the token and checks permission to invoke the agent.\n"
        "The user's stored profile determines their team and access;\n"
        "callers cannot choose another team's identity.")
    ui.pause("  Press Enter to test the access boundary...", context.no_pause)
    expect(client.call("/invoke", team=None, body={"change_summary": context.change_summary}), 401, "Missing identity")
    expect(client.call("/probe", team="blocked"), 403, "Denied identity")
    expect(client.invoke("payments", context.change_summary, headers={"X-Demo-Team": "platform"}), 400, "Spoofed team")
    expect(client.call("/invoke", body={"change_summary": context.change_summary, "team": "platform"}),
           400, "Unexpected request field")
    ui.show_values("Live access checks", [
        ("Missing token", "401 — authentication required"),
        ("Blocked identity", "403 — invocation denied"),
        ("Caller-selected team", "400 — trusted identity cannot be overwritten"),
        ("Unexpected JSON field", "400 — invocation schema rejects extra fields"),
    ])
    ui.pause("  Press Enter to explore streaming and request ownership...", context.no_pause)
    ui.clear_screen()
    ui.box("Streaming with durable request ownership",
        "POST /invoke uses REST API response streaming and server-sent events.\n"
        "The client receives acceptance and heartbeats while the agent works,\n"
        "then a terminal result. HTTP 200 alone does not mean the agent succeeded.\n"
        "This is progress streaming, not token-by-token model output.\n\n"
        "Our Lambda adapter assigns a fresh AgentCore session and records request\n"
        "ownership in DynamoDB. It checks the authenticated user and team, rejects\n"
        "conflicting retries, and admits one active investigation per team.\n\n"
        "After a disconnect, callers can securely check the existing request\n"
        "and retrieve its result without starting another investigation.")
    ui.pause("  Press Enter to stream an investigation...", context.no_pause)
    request_id = uuid4().hex
    response = expect(client.invoke("platform", context.change_summary, request_id), 200, "Investigation")
    if response.body.get("status") != "completed":
        raise RuntimeError("The agent did not complete the investigation")
    events = list(client.last_events)
    if not events or events[0]["event"] != "accepted" or events[-1]["event"] != "result":
        raise RuntimeError("Expected an acceptance event and terminal result")
    expect(client.call(f"/requests/{request_id}"), 200, "Owner result")
    expect(client.call(f"/requests/{request_id}", team="payments"), 404, "Other team's result")
    expect(client.invoke("platform", context.change_summary, request_id), 202, "Duplicate request")
    expect(client.invoke("platform", context.change_summary + " Changed request.", request_id), 409, "Conflicting retry")
    ui.box("How the integration handled this request",
        f"The first stream event arrived after {events[0]['seconds']:.2f} seconds.\n"
        f"The completed result arrived after {events[-1]['seconds']:.2f} seconds.\n"
        "The caller could follow progress while the agent worked.\n\n"
        "When we retried with the same request ID and input, our Lambda adapter\n"
        "returned HTTP 202 pointing to the existing request, without invoking\n"
        "the agent again. Reusing that ID with different input returned HTTP 409\n"
        "because it conflicted with the original request.")
    ui.pause("  Press Enter to probe gateway throttling without calling a model...", context.no_pause)
    ui.clear_screen()
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: client.call("/probe").status_code, range(30)))
    if not all(status in {200, 429} for status in statuses):
        raise RuntimeError(f"Unexpected admission probe outcomes: {statuses}")
    ui.show_values("Gateway admission probe", [
        ("Accepted probes", str(statuses.count(200))),
        ("Throttled probes", f"{statuses.count(429)} — best-effort throttling"),
        ("Model calls", "0"),
    ])
    if 429 not in statuses:
        ui.box("No throttle observed in this burst",
            "Usage-plan throttling is best effort. These probes did not demonstrate\n"
            "a 429; do not present them as evidence that throttling occurred.")
    ui.box("Observe the boundary without logging prompts",
        "API Gateway access logs record request ID, team, route, status, and latency.\n"
        "The adapter links the gateway request ID to the investigation and agent trace.\n"
        "CloudWatch retains these logs for seven days in this demo.")
    ui.concept(
        "API Gateway rejects unauthenticated or unauthorized requests before model "
        "execution and applies team rate limits before traffic reaches the agent. "
        "Runtime policies ensure callers cannot bypass this boundary and invoke the "
        "agent directly.")
    ui.pause("  Press Enter to continue...", context.no_pause)


def import_captures(client, session):
    """Validate remote names before writing manifests for the existing reader."""
    from .observability import session_directory
    response = expect(client.call(f"/captures/{session}"), 200, "Read captures")
    records = response.body.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("No cloud captures found. Run cloud Sections 2–3 with this session first.")
    for record in records:
        if (not isinstance(record, dict)
                or not re.fullmatch(r"[0-9a-f]{32}", str(record.get("trace_id", "")))
                or record.get("demo.session.id") != session):
            raise ValueError("Invalid cloud capture metadata")
    directory = session_directory(session)
    directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        path = directory / f"{record['trace_id']}.json"
        path.write_text(json.dumps(record, indent=2))


def run(args, ui):
    with ExitStack() as cleanup:
        client = CloudClient(args.outputs, args.credentials_file)
        context = CloudContext(args, client, cleanup)
        if context.with_ui:
            from . import browser_demo
            browser_demo.validate_url(client.outputs.get("uiUrl"))
        if args.section is None:
            ui.show_intro(context)
        if not context.with_ui:
            print(f"\n  Cloud capture session: {context.session}")
        if context.with_ui:
            print(f"  Browser application: {client.outputs['uiUrl']}\n")
            ui.box("Browser actions with terminal commentary",
                "Use the browser for investigations, recovery planning, and fault injection\n"
                "in Sections 2–4. Enter advances the commentary, without running those actions.\n"
                "Section 4 reads the latest matching traces, independent of this walkthrough.")
        for number in ([args.section] if args.section else [1, 2, 3, 4]):
            if context.with_ui and number in (2, 3, 4):
                {2: browser_demo.capabilities, 3: browser_demo.routing,
                 4: browser_demo.inspection}[number](context, ui)
            elif number == 4:
                from .inspection import show
                import_captures(client, context.session)
                show(context.session, no_pause=context.no_pause, clear=ui.clear_screen,
                     header=ui.header, box=ui.box, pause=ui.pause, concept=ui.concept,
                     final_prompt=("  Press Enter for the module wrap-up..." if context.full_demo
                                   else "  Press Enter to finish this section..."))
            else:
                context.select(number - 1)
                {1: exposure, 2: lambda c, _: ui.section_1(c),
                 3: lambda c, _: ui.section_2(c)}[number](context, ui)
        if context.full_demo:
            if context.with_ui:
                browser_demo.wrap_up(context, ui)
            else:
                ui.show_wrap_up(context.no_pause, cloud=True)
