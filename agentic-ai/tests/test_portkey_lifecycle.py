import json
import ssl
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from module10.gateway.lifecycle import Lifecycle
from module10.portkey_remote import discovery_context
from module10.control_planes import IntegrationUnavailable


@pytest.fixture
def lifecycle():
    outputs = {"clusterArn": "cluster", "serviceName": "service", "discoveryParameter": "/portkey",
        "scheduleGroup": "schedules", "schedulerRoleArn": "role"}
    ecs, ssm, scheduler, secrets = (Mock() for _ in range(4))
    scheduler.exceptions.ResourceNotFoundException = type("Missing", (Exception,), {})
    return Lifecycle(outputs, ecs=ecs, ssm=ssm, scheduler=scheduler, secrets=secrets)


def test_start_does_not_launch_when_expiry_cannot_be_created(lifecycle):
    lifecycle.scheduler.update_schedule.side_effect = RuntimeError("denied")
    with pytest.raises(RuntimeError, match="denied"):
        lifecycle.start()
    lifecycle.ecs.update_service.assert_not_called()


def test_failed_start_stops_compute_and_invalidates_discovery(lifecycle):
    with pytest.raises(TimeoutError):
        lifecycle.start(timeout=0)
    assert lifecycle.ecs.update_service.call_args_list[-1].kwargs["desiredCount"] == 0
    values = [json.loads(call.kwargs["Value"]) for call in lifecycle.ssm.put_parameter.call_args_list]
    assert values[-1] == {"status": "stopped"}
    assert values[-2] == {"status": "stopped", "expires_at": 0}
    lifecycle.scheduler.delete_schedule.assert_called_once()


def test_failed_stop_keeps_expiry_schedule(lifecycle):
    lifecycle.ecs.update_service.side_effect = RuntimeError("denied")
    with pytest.raises(RuntimeError):
        lifecycle.stop()
    lifecycle.scheduler.delete_schedule.assert_not_called()


def test_expiry_uses_utc_one_shot_and_scoped_role(lifecycle):
    lifecycle.schedule(1800000000)
    schedule = lifecycle.scheduler.update_schedule.call_args.kwargs
    assert schedule["ActionAfterCompletion"] == "DELETE"
    assert schedule["ScheduleExpressionTimezone"] == "UTC"
    assert json.loads(schedule["Target"]["Input"]) == {
        "Cluster": "cluster", "Service": "service", "DesiredCount": 0}


def test_stop_waits_for_deprovisioning_not_just_desired_count(lifecycle, monkeypatch):
    lifecycle.ecs.list_tasks.side_effect = lambda **kw: {
        "taskArns": ["draining"] if kw["desiredStatus"] == "STOPPED" else []}
    lifecycle.ecs.describe_tasks.side_effect = [
        {"tasks": [{"lastStatus": "DEPROVISIONING"}]},
        {"tasks": [{"lastStatus": "STOPPED"}]},
    ]
    sleep = Mock()
    monkeypatch.setattr("module10.gateway.lifecycle.time.sleep", sleep)
    assert lifecycle.wait_stopped() == {"status": "stopped"}
    sleep.assert_called_once()


def test_console_login_reads_only_console_secret(lifecycle, tmp_path, monkeypatch, capsys):
    from module10.gateway import lifecycle as command
    outputs = tmp_path / "gateway-outputs.json"
    lifecycle.outputs.update(consoleSecretArn="console-secret", serviceSecretArn="service-secret")
    outputs.write_text(json.dumps(lifecycle.outputs))
    lifecycle.secrets.get_secret_value.return_value = {"SecretString": "test-console-token"}
    monkeypatch.setattr(command, "Lifecycle", lambda settings: lifecycle)
    monkeypatch.setattr("sys.argv", ["lifecycle", "console-login", "--outputs", str(outputs)])
    command.main()
    lifecycle.secrets.get_secret_value.assert_called_once_with(SecretId="console-secret")
    assert capsys.readouterr().out.strip() == "test-console-token"


def test_discovery_requires_https_ip_and_a_trusted_certificate(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
    entry = {"status": "ready", "url": "https://127.0.0.1:8443", "certificate": cert.read_text()}
    url, context = discovery_context(entry)
    assert url == entry["url"]
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    for url in ("http://127.0.0.1:8443", "https://evil.example:8443", "https://127.0.0.1:8443/redirect"):
        with pytest.raises((ValueError, IntegrationUnavailable)):
            discovery_context({**entry, "url": url})
    with pytest.raises(IntegrationUnavailable, match="stopped"):
        discovery_context({"status": "stopped"})
    with pytest.raises(ssl.SSLError):
        discovery_context({**entry, "certificate": "invalid"})
