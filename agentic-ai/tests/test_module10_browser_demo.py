from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock
from botocore.exceptions import ClientError

import pytest

from module10 import browser_demo, cloud_demo, inspection


def record(request="request-1", section=1, *, planning=True, fallback=False, team="platform", **extra):
    return {
        "demo.session.id": "browser-run", "demo.section": section,
        "timestamp": "2026-09-21T01:00:00Z", "trace_id": "a" * 32,
        "request.id": request, "request.status": "completed", "team.id": team,
        "feature_flag.result.value": planning, "feature_flag.version": "7",
        "feature_flag.provider.name": "LaunchDarkly",
        "agent.planner_executed": planning, "recovery.plan_saved": planning,
        "portkey.fallback_used": fallback, "telemetry.export": "collector-accepted", **extra,
    }


def context():
    client = Mock(outputs={"artifactBucket": "bucket", "runtimeArn": "arn:aws:bedrock:us-east-1:123:runtime/demo"})
    return SimpleNamespace(client=client, session="browser-run", no_pause=False,
                           full_demo=True, browser_evidence={})


def test_latest_matching_evidence_ignores_session_section_and_team():
    rows = [record(planning=False), record("enabled"),
            record("fallback", 2, planning=False, fallback=True),
            record("newer", section=0, team="payments", timestamp="2026-09-21T02:00:00Z",
                   **{"demo.session.id": "other"})]
    original = deepcopy(rows)
    selected = browser_demo.select_evidence(rows[::-1])
    assert set(selected) == {"planning_on", "fallback"}
    assert selected["planning_on"]["request.id"] == "newer"
    assert selected["fallback"]["request.id"] == "fallback"
    assert rows == original


def test_one_request_can_demonstrate_both_outcomes_without_grouping_metadata():
    row = record(fallback=True)
    del row["demo.session.id"], row["demo.section"]
    assert browser_demo.select_evidence([row]) == {"planning_on": row, "fallback": row}


@pytest.mark.parametrize("rows,keys", [
    ([], set()), ([record()], {"planning_on"}),
    ([record(planning=False, fallback=True)], {"fallback"}),
])
def test_empty_and_partial_evidence(rows, keys):
    assert set(browser_demo.select_evidence(rows)) == keys


def test_section_selection_never_configures_backend_in_browser_mode():
    client = Mock()
    args = SimpleNamespace(session=None, no_pause=False, with_ui=True,
                           change_summary="Test", section=None)
    ctx = cloud_demo.CloudContext(args, client, None)
    for section in range(4):
        ctx.select(section)
    assert not client.mock_calls


@pytest.mark.parametrize("changes", [
    {"request.status": "failed"}, {"trace_id": "../bad"}, {"request.id": "bad\nrequest"},
    {"timestamp": "invalid"}, {"timestamp": "2026-01-01"}, {"timestamp": None},
    {"agent.planner_executed": False}, {"recovery.plan_saved": False},
    {"feature_flag.result.value": "true"},
    {"feature_flag.provider.name": "other"}, {"feature_flag.result.value": False},
])
def test_unrelated_incomplete_and_malformed_records_are_not_evidence(changes):
    assert browser_demo.select_evidence([None, {}, record(**changes)]) == {}


def test_browser_sections_only_read_captures_and_advance_commentary(monkeypatch):
    ctx, ui = context(), Mock()
    reader = Mock(return_value=browser_demo.select_evidence([record(fallback=True)]))
    monkeypatch.setattr(browser_demo, "latest_evidence", reader)
    browser_demo.capabilities(ctx, ui)
    browser_demo.routing(ctx, ui)
    assert not ctx.client.mock_calls
    reader.assert_not_called()
    browser_demo.inspection(ctx, ui)
    assert not ctx.client.mock_calls
    reader.assert_called_once_with(ctx.client.outputs)
    assert ui.pause.call_count == 7
    assert ui.clear_screen.call_count == 7
    assert any(call.args[0] == "  Press Enter to review the recorded examples..."
               for call in ui.pause.call_args_list)
    trace_titles = {title for title, _ in browser_demo.EXAMPLES.values()}
    assert [call.args[0] for call in ui.box.call_args_list if call.args[0] in trace_titles] == [
        "Recovery planner enabled", "Model fallback"]
    assert all(call.args[1] is False for call in ui.pause.call_args_list)
    assert {"planning_on", "fallback"} == set(ctx.browser_evidence)
    assert "matching trace found" in wrap_text(ctx)
    assert "Payments comparison: observed" not in wrap_text(ctx)


def wrap_text(ctx):
    ui = Mock()
    browser_demo.wrap_up(ctx, ui)
    return "\n".join(call.args[1] for call in ui.box.call_args_list)


def test_wrap_up_summarizes_each_section_and_service():
    text = wrap_text(context())
    assert "Protect the agent exposure boundary" in text
    assert "Amazon API Gateway" in text
    assert "Release a capability to selected callers" in text
    assert "LaunchDarkly" in text
    assert "Keep recovery planning available" in text
    assert "Portkey AI" in text
    assert "Explain what each caller experienced" in text
    assert "Logz.io" in text


@pytest.mark.parametrize("error", [None, OSError("unavailable"), ValueError("bad metadata"),
    ClientError({"Error": {"Code": "AccessDenied"}}, "ListObjectsV2")])
def test_missing_captures_never_block_inspection(monkeypatch, error):
    ctx, ui = context(), Mock()
    monkeypatch.setattr(browser_demo, "latest_evidence", Mock(return_value={}, side_effect=error))
    browser_demo.inspection(ctx, ui)
    assert ui.pause.call_count == 2
    assert ctx.browser_evidence == {}
    assert "no matching trace found" in wrap_text(ctx)


def test_s3_reader_paginates_all_sessions_and_skips_invalid_or_deleted_records(monkeypatch):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "manifests/old/one.json"}, {"Key": "manifests/old/bad.json"}]},
        {"Contents": [{"Key": "manifests/new/two.json"}, {"Key": "manifests/new/deleted.json"},
                      {"Key": "manifests/new/note.txt"}]},
    ]
    def get_object(*, Bucket, Key):
        assert Bucket == "bucket"
        if Key.endswith("deleted.json"):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        row = record() if "/old/" in Key else record("latest", fallback=True, timestamp="2026-09-22T00:00:00Z")
        return {"Body": BytesIO(b"bad" if Key.endswith("bad.json") else json.dumps(row).encode())}
    s3.get_object.side_effect = get_object
    client = Mock(return_value=s3)
    monkeypatch.setattr(browser_demo.boto3, "client", client)
    evidence = browser_demo.latest_evidence(context().client.outputs)
    assert {row["request.id"] for row in evidence.values()} == {"latest"}
    client.assert_called_once_with("s3", region_name="us-east-1")
    s3.get_paginator.assert_called_once_with("list_objects_v2")
    s3.get_paginator.return_value.paginate.assert_called_once_with(Bucket="bucket", Prefix="manifests/")
    assert s3.get_object.call_count == 4


def test_s3_access_failure_does_not_return_a_partial_latest_result(monkeypatch):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "manifests/one.json"}, {"Key": "manifests/two.json"}]}]
    s3.get_object.side_effect = [
        {"Body": BytesIO(json.dumps(record()).encode())},
        ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")]
    monkeypatch.setattr(browser_demo.boto3, "client", Mock(return_value=s3))
    with pytest.raises(ClientError):
        browser_demo.latest_evidence(context().client.outputs)


@pytest.mark.parametrize("extra,expected", [
    ({}, "confirm ingestion"),
    ({"telemetry.export": "failed"}, "incomplete"),
    ({"telemetry.delivery": "failed"}, "delivery failed"),
])
def test_telemetry_status_does_not_claim_logzio_ingestion(extra, expected):
    ui = Mock()
    browser_demo.show_example(ui, "planning_on", record(**extra))
    text = ui.box.call_args.args[1]
    assert expected in text
    assert "Request ID: request-1" in text and f'Trace ID: {"a" * 32}' in text


@pytest.mark.parametrize("section", [None, 2, 3, 4])
def test_cloud_dispatch_keeps_actions_in_browser(monkeypatch, section):
    client, ui = Mock(), Mock()
    client.outputs = {"uiUrl": "https://example.invalid"}
    client.base_url = "https://api.invalid"
    monkeypatch.setattr(browser_demo, "latest_evidence", Mock(return_value={}))
    monkeypatch.setattr(cloud_demo, "CloudClient", lambda *_: client)
    exposure = Mock()
    monkeypatch.setattr(cloud_demo, "exposure", exposure)
    strict = Mock(side_effect=AssertionError("Strict inspection must not run"))
    monkeypatch.setattr(inspection, "show", strict)
    args = SimpleNamespace(outputs=None, credentials_file=None, session="browser-run",
                           no_pause=False, change_summary="Test", section=section, with_ui=True)
    cloud_demo.run(args, ui)
    client.invoke.assert_not_called()
    assert not client.mock_calls
    assert exposure.call_count == (1 if section is None else 0)
    assert ui.show_intro.call_count == (1 if section is None else 0)
    assert any(call.args[0] == "MODULE 10 COMPLETE" for call in ui.header.call_args_list) == (section is None)
    ui.section_1.assert_not_called()
    ui.section_2.assert_not_called()


def test_regular_cloud_dispatch_keeps_original_configuration(monkeypatch):
    client, ui = Mock(), Mock()
    client.outputs, client.base_url = {}, "https://api.invalid"
    monkeypatch.setattr(cloud_demo, "CloudClient", lambda *_: client)
    args = SimpleNamespace(outputs=None, credentials_file=None, session="browser-run",
                           no_pause=True, change_summary="Test", section=2, with_ui=False)
    cloud_demo.run(args, ui)
    client.configure.assert_called_once_with("browser-run", 1, "portkey")
    ui.section_1.assert_called_once()


@pytest.mark.parametrize("url", [None, "", "http://example.com", "https://", "https://user:password@example.com"])
def test_requires_application_url_before_any_configuration(monkeypatch, url):
    client = Mock(outputs={"uiUrl": url})
    monkeypatch.setattr(cloud_demo, "CloudClient", lambda *_: client)
    args = SimpleNamespace(outputs=None, credentials_file=None, session=None, no_pause=False,
                           change_summary="Test", section=2, with_ui=True)
    with pytest.raises(ValueError, match="uiUrl"):
        cloud_demo.run(args, Mock())
    client.configure.assert_not_called()


@pytest.mark.parametrize("arguments", [
    ["--with-ui", "--mode", "local"],
    ["--with-ui", "--mode", "cloud", "--no-pause"],
])
def test_invalid_cli_combinations_fail_before_cloud_work(monkeypatch, arguments):
    from demos import module10_demo
    monkeypatch.setattr("sys.argv", ["module10_demo.py", *arguments])
    cloud_run = Mock()
    monkeypatch.setattr(cloud_demo, "run", cloud_run)
    with pytest.raises(SystemExit) as exit:
        module10_demo.main()
    assert exit.value.code == 2
    cloud_run.assert_not_called()


@pytest.mark.parametrize("with_ui", [True, False])
def test_standalone_inspection_only_requires_session_for_scripted_mode(monkeypatch, with_ui):
    from demos import module10_demo
    monkeypatch.setattr("sys.argv", ["module10_demo.py", "--mode", "cloud", "--section", "4",
                                  "--outputs", "outputs.json", "--credentials-file", "credentials.json"]
                        + (["--with-ui"] if with_ui else []))
    cloud_run = Mock()
    monkeypatch.setattr(cloud_demo, "run", cloud_run)
    if with_ui:
        module10_demo.main()
        cloud_run.assert_called_once()
    else:
        with pytest.raises(SystemExit):
            module10_demo.main()
        cloud_run.assert_not_called()
