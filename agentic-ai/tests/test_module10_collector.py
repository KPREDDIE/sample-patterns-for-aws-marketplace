"""Automatic collector ownership, failure cleanup, and the single-command demo."""
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
from unittest.mock import Mock

import pytest

from module10 import collector


@pytest.fixture
def startup(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    monkeypatch.setenv("LOGZIO_REGION", "us")
    monkeypatch.setenv("LOGZIO_LOGS_TOKEN", "test-logs")
    monkeypatch.setenv("LOGZIO_TRACES_TOKEN", "test-traces")
    monkeypatch.delenv("MODULE10_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(collector, "validate_settings", Mock())
    monkeypatch.setattr(collector, "install", Mock(return_value=Path("/test/collector")))
    monkeypatch.setattr(collector.subprocess, "run", Mock())
    monkeypatch.setattr(collector.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(collector, "ready", Mock(side_effect=[False, True]))
    return process


@pytest.mark.parametrize("failure", [None, ValueError, KeyboardInterrupt])
def test_owned_collector_stops_and_restores_environment(startup, failure):
    def run():
        with collector.managed_collector(required=True) as process:
            assert process is startup
            assert os.environ["MODULE10_OTLP_ENDPOINT"] == collector.DEFAULT_ENDPOINT
            if failure:
                raise failure()
    if failure:
        with pytest.raises(failure):
            run()
    else:
        run()
    startup.terminate.assert_called_once()
    startup.wait.assert_called_once_with(timeout=15)
    assert "MODULE10_OTLP_ENDPOINT" not in os.environ


def test_reuses_receiver_without_installing_or_stopping_it(startup, monkeypatch):
    monkeypatch.setenv("MODULE10_OTLP_ENDPOINT", "http://127.0.0.1:9999")
    monkeypatch.setattr(collector, "ready", Mock(return_value=True))
    with collector.managed_collector(required=True) as owned:
        assert owned is None
    collector.install.assert_not_called()
    collector.subprocess.Popen.assert_not_called()
    startup.terminate.assert_not_called()
    assert os.environ["MODULE10_OTLP_ENDPOINT"] == "http://127.0.0.1:9999"


def test_optional_telemetry_does_not_start_a_process(monkeypatch):
    install = Mock(side_effect=AssertionError("Unexpected installation"))
    monkeypatch.setattr(collector, "install", install)
    with collector.managed_collector() as owned:
        assert owned is None
    install.assert_not_called()


@pytest.mark.parametrize("failure", ["exited", "timeout"])
def test_startup_failure_cleans_up_owned_process(startup, monkeypatch, failure):
    monkeypatch.setattr(collector, "ready", Mock(return_value=False))
    if failure == "exited":
        startup.poll.return_value = 1
    with pytest.raises(ValueError, match="Collector (exited|startup timed out)"):
        with collector.managed_collector(required=True, startup_timeout=0):
            pytest.fail("Must not enter the demo before readiness")
    if failure == "timeout":
        startup.terminate.assert_called_once()
    assert "MODULE10_OTLP_ENDPOINT" not in os.environ


def test_shutdown_kills_and_reaps_a_stuck_process(startup):
    startup.wait.side_effect = [subprocess.TimeoutExpired("collector", 15), 0]
    with collector.managed_collector(required=True):
        pass
    startup.kill.assert_called_once()
    assert startup.wait.call_args_list[-1].kwargs == {"timeout": 5}


def test_invalid_configuration_does_not_launch(startup):
    collector.subprocess.run.side_effect = subprocess.CalledProcessError(1, "validate")
    with pytest.raises(ValueError, match="configuration validation failed"):
        with collector.managed_collector(required=True):
            pytest.fail("Invalid configuration must not start a demo")
    collector.subprocess.Popen.assert_not_called()


def test_missing_credentials_fail_before_install(monkeypatch):
    monkeypatch.setattr(collector, "ready", lambda _: False)
    install = Mock()
    monkeypatch.setattr(collector, "install", install)
    with pytest.raises(ValueError, match="LOGZIO_LOGS_TOKEN"):
        with collector.managed_collector(required=True):
            pass
    install.assert_not_called()


def test_unavailable_remote_endpoint_is_not_replaced(startup, monkeypatch):
    monkeypatch.setenv("MODULE10_OTLP_ENDPOINT", "https://collector.example")
    monkeypatch.setattr(collector, "ready", lambda _: False)
    with pytest.raises(ValueError, match="configured collector is unavailable"):
        with collector.managed_collector(required=True):
            pass
    collector.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP])
def test_termination_unwinds_owned_process_and_restores_handlers(startup, number):
    before = signal.getsignal(number)
    with pytest.raises(SystemExit) as error:
        with collector.managed_collector(required=True):
            signal.getsignal(number)(number, None)
    assert error.value.code == 128 + number
    assert signal.getsignal(number) == before
    startup.terminate.assert_called_once()


@pytest.mark.parametrize("arguments,failure", [
    ([], None), (["--seed-observability"], None), (["--section", "1"], ValueError),
    ([], KeyboardInterrupt),
])
def test_demo_owns_native_collector_through_final_flush(tmp_path, monkeypatch, arguments, failure):
    """Run the cached real binary with a local debug exporter; no vendor traffic."""
    from demos import module10_demo as demo
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    binary = collector.CACHE / f"{system}_{machine}" / "otelcol-contrib"
    if not binary.exists():
        pytest.skip("Native integration needs the pinned collector cached by local setup")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{reserved.getsockname()[1]}"
    # Keep the receiver/health addresses that managed_collector substitutes.
    (tmp_path / "collector.yaml").write_text("""
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
exporters:
  debug: {}
extensions:
  health_check:
    endpoint: 127.0.0.1:13133
service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
    logs:
      receivers: [otlp]
      exporters: [debug]
""")
    monkeypatch.setenv("MODULE10_OTLP_ENDPOINT", endpoint)
    monkeypatch.setattr(collector, "ROOT", tmp_path)
    monkeypatch.setattr(collector, "install", lambda: binary)
    monkeypatch.setattr(collector, "validate_settings", lambda **_: None)
    monkeypatch.setattr(demo.sys, "argv", ["demo", "--mode", "local", *arguments])
    events = []
    original_stop = collector.stop

    def stop(process):
        events.append("stop")
        original_stop(process)
        assert process.poll() is not None

    def run(*_):
        assert collector.ready(endpoint)
        try:
            if failure:
                raise failure()
        finally:
            events.append("flush")

    monkeypatch.setattr(collector, "stop", stop)
    monkeypatch.setattr(demo, "_run_demo", run)
    if failure:
        with pytest.raises(SystemExit if failure is ValueError else failure):
            demo.main()
    else:
        demo.main()
    assert events == (["flush", "flush", "stop"] if arguments == ["--seed-observability"] else ["flush", "stop"])
    assert not collector.ready(endpoint)
