"""Verify transaction evidence, actual OTLP payloads, and read-only inspection."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread
from types import SimpleNamespace

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest

from module10.flags import set_recovery_planning_enabled
from module10.inspection import selected_records
from module10.observability import Observations, read_session, validate_session
from module10.reviewer import ReviewService, TeamReviewRequest
from test_module10_reviews import launch, responses_client, investigate, wait_for_planning, TASK
from test_module10_portkey import routed


@pytest.fixture
def collector(monkeypatch, tmp_path):
    state = SimpleNamespace(traces=[], logs=[], code=200)

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/traces":
                state.traces.append(ExportTraceServiceRequest.FromString(body))
            elif self.path == "/v1/logs":
                state.logs.append(ExportLogsServiceRequest.FromString(body))
            else:
                raise AssertionError(self.path)
            self.send_response(state.code)
            self.end_headers()
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("MODULE10_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("MODULE10_ARTIFACT_DIR", str(tmp_path))
    yield state
    server.shutdown()
    server.server_close()
    worker.join()


def attrs(items):
    def value(v):
        field = v.WhichOneof("value")
        return [value(x) for x in v.array_value.values] if field == "array_value" else getattr(v, field)
    return {a.key: value(a.value) for a in items}


def exported(collector):
    spans = [s for batch in collector.traces for r in batch.resource_spans for scope in r.scope_spans for s in scope.spans]
    logs = [log for batch in collector.logs for r in batch.resource_logs for scope in r.scope_logs for log in scope.log_records]
    return spans, logs


def test_snapshot_pins_actual_revision_during_flag_change(launch, responses_client):
    reviewer, state = responses_client
    service = ReviewService(launch, reviewer, state.plans)
    revision = launch.evaluate("platform")["version"]
    def flip(_):
        if len(state.requests) == 1:
            set_recovery_planning_enabled(launch.path, True)
            wait_for_planning(launch, True)
    state.hook = flip
    result = investigate(service)
    assert result.body["release"]["version"] == revision
    assert result.body["capabilities"]["recovery_planning"] is False
    assert result.body["recovery_plan"] is None
    next_result = investigate(service)
    assert next_result.body["release"]["version"] == revision + 1
    assert next_result.body["recovery_plan"] is not None
    assert result.body["service_version"] == next_result.body["service_version"]
    assert result.body["behavior"]["version"] != next_result.body["behavior"]["version"]


def test_correlated_otlp_rows_and_nested_spans(collector, launch, responses_client):
    reviewer, state = responses_client
    telemetry = Observations("capture", 1)
    service = ReviewService(launch, reviewer, state.plans, telemetry)
    set_recovery_planning_enabled(launch.path, True)
    wait_for_planning(launch, True)
    try:
        results = [
            service.review(TeamReviewRequest(team, TASK + " secret=private", team, f"{team}-operator"))
            for team in ("platform", "payments")
        ]
        telemetry.flush()
        spans, logs = exported(collector)
        assert len(logs) == 2
        assert {log.trace_id.hex() for log in logs} == {r.body["trace_id"] for r in results}
        assert all(attrs(log.attributes)["trace_id"] == log.trace_id.hex() for log in logs)
        assert all(attrs(log.attributes)["span_id"] == log.span_id.hex() for log in logs)
        rows = [attrs(log.attributes) for log in logs]
        assert {r["user.id"] for r in rows} == {"platform-operator", "payments-operator"}
        assert len({r["feature_flag.version"] for r in rows}) == 1
        assert [r["agent.planner_executed"] for r in rows] == [True, False]
        assert [r["agent.behavior_version"] for r in rows] == [
            r.body["behavior"]["version"] for r in results
        ]
        # Logz.io expands dotted attributes into object paths. Check the union:
        # separate records can also conflict in the same index mapping.
        keys = set().union(*(attrs(item.attributes) for item in [*logs, *spans]))
        for key in keys:
            parts = key.split(".")
            assert not any(".".join(parts[:i]) in keys for i in range(1, len(parts))), key
        planner = next(s for s in spans if s.name == "agent.execute" and attrs(s.attributes)["agent.role"] == "Recovery Planner")
        parent = next(s for s in spans if s.span_id == planner.parent_span_id)
        assert parent.name == "tool.execute"
        assert attrs(parent.attributes)["tool.name"] == "delegate_recovery_planning"
        roots = [s for s in spans if s.name == "POST /invoke"]
        assert all(attrs(s.attributes)["request.status"] == "completed" for s in roots)
        assert all(s.start_time_unix_nano < s.end_time_unix_nano for s in spans)
        serialized = str(collector.traces) + str(collector.logs)
        assert "secret=private" not in serialized
        assert "test-secret-key" not in serialized
        assert "Never claim that" not in serialized
        assert all(r["telemetry.export"] == "collector-accepted" for r in read_session("capture"))
    finally:
        telemetry.close()


def test_concurrent_requests_do_not_share_context(collector, launch, responses_client):
    reviewer, state = responses_client
    telemetry = Observations("parallel", 1)
    service = ReviewService(launch, reviewer, state.plans, telemetry)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: investigate(service, request_id=f"request-{n}"), range(4)))
        telemetry.flush()
        spans, logs = exported(collector)
        assert len({r.body["trace_id"] for r in results}) == 4
        for log in logs:
            children = [s for s in spans if s.trace_id == log.trace_id]
            assert len([s for s in children if s.name == "POST /invoke"]) == 1
            ids = {s.span_id for s in children}
            assert all(not s.parent_span_id or s.parent_span_id in ids for s in children)
        assert len(logs) == 4
    finally:
        telemetry.close()


def test_failed_workflow_is_a_failed_transaction(collector, launch, responses_client):
    reviewer, state = responses_client
    state.status_code = 401
    telemetry = Observations("failed", 1)
    service = ReviewService(launch, reviewer, state.plans, telemetry)
    try:
        result = investigate(service)
        assert result.status_code == 502
        telemetry.flush()
        spans, logs = exported(collector)
        root = next(s for s in spans if s.name == "POST /invoke")
        assert root.status.code == 2  # OTLP ERROR.
        assert attrs(logs[0].attributes)["request.status"] == "failed"
        assert attrs(logs[0].attributes)["recovery.plan_saved"] is False
    finally:
        telemetry.close()


def test_collector_rejection_never_changes_review_result(collector, launch, responses_client):
    reviewer, state = responses_client
    collector.code = 400
    telemetry = Observations("rejected", 1)
    service = ReviewService(launch, reviewer, state.plans, telemetry)
    try:
        assert investigate(service).status_code == 200
        telemetry.flush()
        assert telemetry.export_status == "failed"
        assert read_session("rejected")[0]["telemetry.export"] == "failed"
    finally:
        telemetry.close()


def test_gateway_attempts_are_correlated_with_real_timing(collector, routed):
    service, state = routed
    telemetry = Observations("fallback", 2)
    service.telemetry = telemetry
    try:
        service.reviewer.gateway.set_drill(True)
        result = investigate(service)
        assert result.status_code == 200
        telemetry.flush()
        spans, logs = exported(collector)
        attempts = [s for s in spans if s.name == "portkey.attempt"]
        injected = next(s for s in attempts if attrs(s.attributes)["demo.simulated"])
        assert injected.status.code == 2
        assert attrs(injected.attributes)["http.response.status_code"] == 429
        assert "gen_ai.response.model" not in attrs(injected.attributes)
        assert attrs(injected.attributes)["portkey.target"] == "config.targets[1].targets[0]"
        sibling = next(s for s in attempts if s.parent_span_id == injected.parent_span_id and s.span_id != injected.span_id)
        assert attrs(sibling.attributes)["gen_ai.response.model"].endswith("luna")
        assert attrs(sibling.attributes)["portkey.target"] == "config.targets[1].targets[1]"
        assert injected.end_time_unix_nano <= sibling.start_time_unix_nano
        parent = next(s for s in spans if s.span_id == injected.parent_span_id)
        assert attrs(parent.attributes)["gen_ai.request.model"] == "recovery-planner"
        assert parent.start_time_unix_nano <= injected.start_time_unix_nano
        assert sibling.end_time_unix_nano <= parent.end_time_unix_nano
        assert next(s for s in spans if s.name == "POST /invoke").status.code != 2
        assert attrs(logs[0].attributes)["portkey.fallback_used"] is True
        tool_names = [attrs(s.attributes)["tool.name"] for s in spans if s.name == "tool.execute"]
        assert tool_names.count("get_blast_radius") == 1
        assert tool_names.count("check_rollback_readiness") == 1
        assert len({c["call_id"] for c in result.body["model_calls"]}) == 5
    finally:
        telemetry.close()


@pytest.mark.parametrize("legacy_caller", [False, True])
def test_section_three_reads_capture_without_starting_runtime(collector, launch, responses_client, routed, monkeypatch, capsys, legacy_caller):
    from demos import module10_demo as demo
    reviewer, state = responses_client
    first = Observations("inspection", 1)
    service = ReviewService(launch, reviewer, state.plans, first)
    # The routed fixture already enabled this flag and waited for the SDK.
    # Rewriting it here can race the two requests onto different revisions.
    for team in ("platform", "payments"):
        investigate(service, team)
    first.close()
    second = Observations("inspection", 2)
    gateway_service, _ = routed
    gateway_service.telemetry = second
    investigate(gateway_service)
    gateway_service.reviewer.gateway.set_drill(True)
    investigate(gateway_service)
    second.close()
    rows, (planner, payments), (normal, fallback) = selected_records("inspection")
    assert len(rows) == 4
    def forbidden(*args, **kwargs):
        pytest.fail("Section 3 must not construct a runtime")
    monkeypatch.setattr(demo, "build_runtime", forbidden)
    monkeypatch.setenv("MODULE10_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setattr(demo.sys, "argv", ["demo", "--section", "3", "--session", "inspection", "--no-pause"])
    screens = []
    monkeypatch.setattr(demo, "clear_screen", lambda: screens.append(True))
    if legacy_caller:
        from module10.inspection import show
        show("inspection", no_pause=True, clear=demo.clear_screen,
             header=demo.header, box=demo.box, pause=demo.pause)
    else:
        demo.main()
    assert len(screens) == 3
    output = capsys.readouterr().out
    assert "EXPLAIN THE SUCCESSFUL FALLBACK" in output
    introduction, scenarios = output.split("EXPLAIN THE CAPABILITY DECISION", 1)
    assert planner["trace_id"] not in introduction
    assert fallback["trace_id"] not in introduction
    assert f'Trace ID: {planner["trace_id"]}' in scenarios
    assert f'Trace ID: {fallback["trace_id"]}' in scenarios
    assert payments["trace_id"] not in output
    assert normal["trace_id"] not in output
    assert output.count("Trace ID:") == 2
    assert output.count("Module 10 Concept") == 1
    assert "jaeger" not in output
    assert "Explore search:" not in output and "Explore query:" not in output


@pytest.mark.parametrize("session", [None, "operator-demo"])
def test_full_local_demo_connects_all_sections_on_one_endpoint(
    collector, responses_client, routed, monkeypatch, capsys, session,
):
    from demos import module10_demo as demo
    from module10.flags import DEFAULT_FLAGS_PATH
    from module10.portkey_local import PortkeyReviewer

    gateway_service, gateway_state = routed
    original_flags = DEFAULT_FLAGS_PATH.read_bytes()
    original_build, original_request = demo.build_runtime, demo.DemoContext.request
    runtimes, requests, headings, prompts = [], [], [], []

    def build(*args, **kwargs):
        runtime = original_build(*args, **kwargs)
        runtimes.append(runtime)
        return runtime

    def request(context, team):
        result = original_request(context, team)
        requests.append((context.runtime.telemetry.section, context.base_url, team, result))
        return result

    def enter(prompt):
        prompts.append(prompt)
        return ""

    # Keep real SDKs, file targeting, HTTP endpoint, OTLP, and Portkey. Only
    # model responses use the existing scripted test upstreams.
    monkeypatch.setattr(PortkeyReviewer, "from_env", classmethod(lambda cls: gateway_service.reviewer))
    monkeypatch.setattr(demo, "build_runtime", build)
    monkeypatch.setattr(demo.DemoContext, "request", request)
    monkeypatch.setattr(demo, "header", lambda text, color="cyan": headings.append(text))
    monkeypatch.setattr(demo, "clear_screen", lambda: None)
    monkeypatch.setattr("builtins.input", enter)
    argv = ["demo", "--mode", "local"] + (["--session", session] if session else [])
    monkeypatch.setattr(demo.sys, "argv", argv)
    demo.main()
    output = capsys.readouterr().out

    assert len(runtimes) == 2  # No runtime or extra invocation for Section 3.
    actual_session = runtimes[0].telemetry.session
    if session:
        assert actual_session == session
    else:
        assert actual_session.startswith("module10-")
    assert all(r.telemetry.session == actual_session for r in runtimes)
    rows, _, (_, fallback) = selected_records(actual_session)
    assert len(rows) == len(requests) == 5
    assert [r[0] for r in requests] == [1, 1, 1, 2, 2]
    assert len({r[1] for r in requests}) == 1
    assert [r[2] for r in requests] == ["platform", "platform", "payments", "platform", "platform"]
    assert all(r[3].status_code == 200 for r in requests)
    assert requests[-1][3].body["trace_id"] == fallback["trace_id"]
    assert requests[-1][3].body["routing"]["fallback_used"] is True
    assert len(responses_client[1].requests) == 9
    assert len(gateway_state.requests) == 10
    assert headings[0] == "MODULE 10 — AGENT EXPOSURE AND INTEGRATION"
    assert headings[-4:] == [
        "SEE WHAT THE CALLER RECEIVED", "EXPLAIN THE CAPABILITY DECISION",
        "EXPLAIN THE SUCCESSFUL FALLBACK", "MODULE 10 COMPLETE",
    ]
    assert "SECTION 1 COMPLETE" not in headings
    assert "  Press Enter to explore model resilience..." in prompts
    assert "  Press Enter to understand the caller's experience..." in prompts
    assert all(not any(vendor in p for vendor in ("LaunchDarkly", "Portkey", "Logz.io")) for p in prompts)
    assert output.count("Module 10 Concept:") == 3
    assert prompts[-2:] == ["  Press Enter for the module wrap-up...", "  Press Enter to finish..."]
    assert f'Trace ID: {fallback["trace_id"]}' in output
    assert "jaeger" not in output
    assert DEFAULT_FLAGS_PATH.read_bytes() == original_flags
    assert gateway_service.reviewer.gateway.process.poll() is not None


@pytest.mark.parametrize("arguments", [
    [], ["--section", "1"], ["--section", "2"], ["--seed-observability"],
])
def test_capture_checks_collector_before_starting_runtime(monkeypatch, capsys, arguments):
    from demos import module10_demo as demo
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))  # Reserve a port without listening.
        monkeypatch.setenv("MODULE10_OTLP_ENDPOINT",
                           f"http://127.0.0.1:{unavailable.getsockname()[1]}")
        monkeypatch.setattr(demo.sys, "argv", ["demo", "--mode", "local", *arguments])
        monkeypatch.setattr(demo, "build_runtime",
                            lambda *a, **kw: pytest.fail("No runtime before collector check"))
        with pytest.raises(SystemExit) as exit:
            demo.main()
    assert exit.value.code == 2
    assert "LOGZIO_LOGS_TOKEN" in capsys.readouterr().err


def test_full_demo_requires_telemetry_before_starting_runtime(monkeypatch, capsys):
    from demos import module10_demo as demo
    monkeypatch.setattr("module10.collector.ready", lambda _: False)
    monkeypatch.setattr(demo.sys, "argv", ["demo", "--mode", "local"])
    monkeypatch.setattr(demo, "build_runtime",
                        lambda *a, **kw: pytest.fail("No runtime before telemetry setup"))
    with pytest.raises(SystemExit) as exit:
        demo.main()
    assert exit.value.code == 2
    assert "LOGZIO_LOGS_TOKEN" in capsys.readouterr().err


def test_session_validation_and_missing_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("MODULE10_ARTIFACT_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        validate_session("../../unsafe")
    with pytest.raises(ValueError, match="No Section 1 capture"):
        selected_records("missing")


def test_optional_user_header_validation():
    request = TeamReviewRequest.from_http({"change_summary": TASK}, "platform", "platform-operator")
    assert request.user_id == "platform-operator"
    assert TeamReviewRequest.from_http({"change_summary": TASK}, "platform").user_id is None
    with pytest.raises(ValueError):
        TeamReviewRequest.from_http({"change_summary": TASK}, "platform", " ")
