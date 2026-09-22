"""Trust-boundary and packaging checks for the cloud deployment."""
import io
import json
from pathlib import Path
import zipfile

import pytest

from module10.cloud_app import validate_envelope
from module10.deployment.build_image import source_archive
from module10.models import ReviewResponse


def envelope():
    return {"identity": {"team": "platform", "principal": "test-user"},
            "request": {"change_summary": "Investigate the deployment", "request_id": "request-1"}}


@pytest.mark.parametrize("field,value", [
    ("team", "../payments"), ("principal", "user/other"),
    ("team", ""), ("principal", {"sub": "user"}),
])
def test_reject_identity_path_injection(field, value):
    payload = envelope()
    payload["identity"][field] = value
    with pytest.raises(ValueError):
        validate_envelope(payload)


def test_request_cannot_override_identity_or_capability():
    for field in ("team", "principal", "planning_enabled"):
        payload = envelope()
        payload["request"][field] = "platform"
        with pytest.raises(ValueError):
            validate_envelope(payload)


def test_request_id_cannot_escape_artifact_prefix():
    payload = envelope()
    payload["request"]["request_id"] = "../../other"
    with pytest.raises(ValueError):
        validate_envelope(payload)


def test_valid_internal_request_keeps_trusted_identity():
    request, context = validate_envelope(envelope())
    assert (request.team, request.user_id, request.request_id) == ("platform", "test-user", "request-1")
    assert context == {"model_backend": "portkey"}
    payload = envelope()
    payload["context"] = {"model_backend": "bedrock"}
    assert validate_envelope(payload)[1]["model_backend"] == "portkey"


def test_hosted_runtime_never_falls_back_to_direct_bedrock(monkeypatch):
    from unittest.mock import Mock
    from module10.control_planes import IntegrationUnavailable
    from module10.reviewer import BedrockReviewer
    from module10.runtime import build_runtime
    direct = Mock()
    monkeypatch.setattr(BedrockReviewer, "from_env", direct)
    for name in ("MODULE10_PORTKEY_DISCOVERY", "MODULE10_PORTKEY_SECRET", "MODULE10_PORTKEY_EVIDENCE_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(IntegrationUnavailable, match="Hosted Portkey configuration"):
        build_runtime("local", model_backend="bedrock",
            hosted_identity={"team": "platform", "principal": "test", "request_id": "test"})
    direct.assert_not_called()


def test_stopped_gateway_persists_one_terminal_failure(monkeypatch):
    from unittest.mock import Mock
    from module10 import cloud_app
    from module10.control_planes import IntegrationUnavailable
    request, context = validate_envelope(envelope())
    monkeypatch.setenv("MODULE10_ARTIFACT_BUCKET", "artifacts")
    monkeypatch.delenv("MODULE10_FLAGS_KEY", raising=False)
    monkeypatch.setattr(cloud_app, "build_runtime", Mock(side_effect=IntegrationUnavailable("stopped")))
    storage = Mock()
    monkeypatch.setattr(cloud_app.boto3, "client", lambda _: storage)
    response = cloud_app.investigate(request, context)
    assert response.status_code == 503
    assert response.body["model_calls"] == []
    assert "start it before invoking agents" in response.body["error"]
    stored = storage.put_object.call_args.kwargs
    assert stored["Key"] == "results/platform/test-user/request-1.json"
    assert json.loads(stored["Body"]) == response.body


def test_build_archive_excludes_credentials_state_and_cached_dependencies(tmp_path):
    files = {
        "module10/cloud_app.py": "source",
        "module10/deployment/Dockerfile": "build",
        "module10/.env": "secret",
        "module10/.env.production": "secret",
        "module10/.cache/portkey/token.json": "secret",
        "module10/.local/workshop/credentials.json": "secret",
        "module10/.local/workshop/stack.yaml": "account settings",
        "module10/.local/workshop/state/checkpoint.json": "encrypted state",
        "module10/.local/workshop/pulumi-venv/lib/dependency.py": "local dependency",
        "module10/artifacts/result.json": "private",
        "module10/deployment/pulumi/Pulumi.demo.yaml": "local account settings",
        "module10/credentials.json": "secret",
        "module10/deployment/outputs.json": "account settings",
        "module10/gateway/stack.yaml": "local deployment",
        "module10/gateway/build-report.json": "generated build",
        "module10/ui/config.json": "generated public endpoints",
        "module10/gateway/node_modules/dependency/index.js": "dependency",
        ".dockerignore": "filters",
    }
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (tmp_path / "module10/linked_credentials.py").symlink_to(tmp_path / "module10/credentials.json")
    data = source_archive(tmp_path)
    assert source_archive(tmp_path) == data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"module10/cloud_app.py", "module10/deployment/Dockerfile", ".dockerignore"}


@pytest.mark.parametrize("trace_id,session", [
    ("../../outside", "demo"), ("a" * 32, "another-session"),
])
def test_capture_import_rejects_untrusted_paths_and_sessions(tmp_path, monkeypatch, trace_id, session):
    from module10.cloud_demo import import_captures
    monkeypatch.setenv("MODULE10_ARTIFACT_DIR", str(tmp_path))

    class Client:
        def call(self, path):
            return ReviewResponse(200, {"records": [{"trace_id": trace_id, "demo.session.id": session}]})

    with pytest.raises(ValueError, match="Invalid cloud capture"):
        import_captures(Client(), "demo")
    assert not list(tmp_path.iterdir())


def test_capture_import_can_be_read_by_existing_inspection(tmp_path, monkeypatch):
    from module10.cloud_demo import import_captures
    from module10.observability import read_session
    monkeypatch.setenv("MODULE10_ARTIFACT_DIR", str(tmp_path))
    record = {"trace_id": "a" * 32, "demo.session.id": "demo", "timestamp": "2026-01-01T00:00:00Z"}

    class Client:
        def call(self, path):
            return ReviewResponse(200, {"records": [record]})

    import_captures(Client(), "demo")
    assert read_session("demo") == [record]


def test_cloud_rejects_caller_supplied_control_context():
    for field, value in [("section", True), ("model_backend", "unapproved"),
                         ("capture_session", "../../elsewhere")]:
        payload = envelope()
        payload["context"] = {field: value}
        with pytest.raises(ValueError):
            validate_envelope(payload)


@pytest.fixture
def cloud_client(tmp_path):
    from module10.cloud_client import CloudClient
    outputs = tmp_path / "outputs.json"
    outputs.write_text(json.dumps({"apiUrl": "https://example.invalid/demo"}))
    credentials = tmp_path / "credentials.json"
    credentials.write_text("{}")
    return CloudClient(outputs, credentials)


def test_stream_delivers_progress_before_terminal_result(cloud_client, monkeypatch):
    import urllib.request
    stream = io.BytesIO(
        b'event: accepted\ndata: {"request_id":"test"}\n\n'
        b'event: heartbeat\ndata: {"request_id":"test"}\n\n'
        b'event: result\ndata: {"status":"completed"}\n\n')
    stream.headers = {"Content-Type": "text/event-stream"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: stream)
    seen = []
    response = cloud_client.call("/invoke", team=None, body={}, on_event=lambda event: seen.append(event["event"]))
    assert seen == ["accepted", "heartbeat", "result"]
    assert response.status_code == 200
    assert response.body["status"] == "completed"


def test_disconnected_stream_is_not_reported_as_completed(cloud_client, monkeypatch):
    import urllib.request
    stream = io.BytesIO(b'event: accepted\ndata: {"request_id":"test"}\n\n')
    stream.headers = {"Content-Type": "text/event-stream"}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: stream)
    with pytest.raises(RuntimeError, match="without a terminal result"):
        cloud_client.call("/invoke", team=None, body={})


def test_non_json_gateway_failure_preserves_http_outcome(cloud_client, monkeypatch):
    import urllib.error
    import urllib.request

    def denied(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid/demo", 403, "Forbidden", {},
                                     io.BytesIO(b"<html>Forbidden</html>"))

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    response = cloud_client.call("/probe", team=None)
    assert response.status_code == 403
    assert response.body == {"error": "HTTP 403"}


def test_deployment_state_guard_allows_only_local_directory_or_external_paths(tmp_path):
    from module10.deployment.paths import MODULE_ROOT, deployment_path
    source = Path(__file__).resolve()
    root = next(parent for parent in source.parents if (parent / ".git").exists())
    with pytest.raises(ValueError, match="outside the Git repository"):
        deployment_path(root / "credentials.json")
    for relative in ("credentials.json", ".local-other/credentials.json", ".local/../credentials.json"):
        with pytest.raises(ValueError, match="module10/.local/"):
            deployment_path(MODULE_ROOT / relative)
    local = MODULE_ROOT / ".local/workshop/credentials.json"
    assert deployment_path(local) == local
    assert deployment_path(tmp_path / "credentials.json") == (tmp_path / "credentials.json").resolve()


def test_deployment_state_guard_rejects_symlink_into_source(tmp_path, monkeypatch):
    from module10.deployment import paths
    repo = tmp_path / "repo"
    module = repo / "agentic-ai/module10"
    local = module / ".local"
    local.mkdir(parents=True)
    (repo / ".git").mkdir()
    (local / "escape").symlink_to(module, target_is_directory=True)
    monkeypatch.setattr(paths, "MODULE_ROOT", module)
    with pytest.raises(ValueError, match="module10/.local/"):
        paths.deployment_path(local / "escape/credentials.json")


def test_local_deployment_files_are_git_ignored():
    import subprocess
    from module10.deployment.paths import MODULE_ROOT
    for name in ("credentials.json", "portkey-stack.yaml", "outputs.json",
                 ".env", "state/checkpoint.json", "pulumi-venv/lib/dependency.py"):
        result = subprocess.run(["git", "check-ignore", "-q", "--",
            str(MODULE_ROOT / ".local/workshop" / name)], cwd=MODULE_ROOT)
        assert result.returncode == 0, name
