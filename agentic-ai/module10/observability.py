"""Request-level evidence, optional OTLP export, and resumable demo sessions."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from .telemetry import TraceRecorder

CURRENT = ContextVar("module10_observation", default=None)
ROOT = Path(__file__).parent


def load_environment(path: Path | None = None):
    """Read only this demo's telemetry settings; do not alter AWS configuration."""
    path = path or ROOT.parent / ".env"
    if not path.exists():
        return
    from dotenv import dotenv_values
    allowed = {
        "LOGZIO_REGION", "LOGZIO_LOGS_TOKEN", "LOGZIO_TRACES_TOKEN",
        "LOGZIO_APP_URL", "MODULE10_OTLP_ENDPOINT", "MODULE10_DEMO_SESSION",
    }
    for key, value in dotenv_values(path, interpolate=False).items():
        if key in allowed and value:
            os.environ.setdefault(key, value)


def fingerprint(parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def build_version() -> str:
    return os.getenv("MODULE10_BUILD_ID") or fingerprint({
        str(p.relative_to(ROOT)): p.read_text()
        for pattern in ("*.py", "gateway/*.mjs", "gateway/*.js", "gateway/routing.json")
        for p in sorted(ROOT.glob(pattern))
    })


def validate_session(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Session must contain 1–64 letters, numbers, underscores or hyphens.")
    return value


def session_directory(session: str) -> Path:
    base = Path(os.getenv("MODULE10_ARTIFACT_DIR") or ROOT / "artifacts")
    return base / "sessions" / validate_session(session)


def read_session(session: str) -> list[dict]:
    directory = session_directory(session)
    return sorted(
        (json.loads(p.read_text()) for p in directory.glob("*.json")),
        key=lambda r: r["timestamp"],
    )


def span(name: str, attributes: dict | None = None):
    current = CURRENT.get()
    return current.span(name, attributes) if current else nullcontext()


def correlation() -> dict:
    current = CURRENT.get()
    if current is None:
        return {}
    return {"trace_id": current.trace_id, "request_id": current.attributes["request.id"]}


@contextmanager
def model_turn(record: dict):
    with span("model.turn", {
        "agent.role": record["agent"],
        "model.call_id": record["call_id"],
        "gen_ai.request.model": record["requested_model"],
    }) as capture:
        try:
            yield
        finally:
            if capture:
                for key, value in {
                    "gen_ai.request.model": record["requested_model"],
                    "gen_ai.response.model": record.get("model"),
                    "gen_ai.response.id": record.get("response_id"),
                    "model.status": record["status"],
                    "portkey.target": record.get("gateway_target"),
                    "portkey.fallback_used": record.get("fallback_used", False),
                }.items():
                    capture.set_attribute(key, value)
                for key, value in (record.get("usage") or {}).items():
                    if isinstance(value, int):
                        capture.set_attribute(f"gen_ai.usage.{key}", value)
                if record["status"] != "completed":
                    capture.mark_error("model_incomplete")
                current = CURRENT.get()
                for index, attempt in enumerate(record.get("attempts", [])):
                    start = attempt["start_time_unix_ms"] * 1_000_000
                    end = attempt["end_time_unix_ms"] * 1_000_000
                    attributes = {
                        "agent.role": record["agent"],
                        "model.call_id": record["call_id"],
                        "portkey.attempt": index + 1,
                        "gen_ai.request.model": attempt["model"],
                        "gen_ai.response.model": attempt.get("served_model"),
                        "gen_ai.response.id": attempt.get("response_id"),
                        "http.response.status_code": attempt["status_code"],
                        "failure.source": attempt["source"],
                        "demo.simulated": attempt["source"] == "injected",
                        "portkey.target": attempt.get("gateway_target"),
                    }
                    with current.span("portkey.attempt", attributes, start_time_ns=start, end_time_ns=end) as child:
                        if attempt["status_code"] >= 400:
                            child.mark_error("simulated_429" if attempt["source"] == "injected" else attempt["source"])


class Observations(TraceRecorder):
    """Exports only explicitly selected metadata; never model content or headers."""

    def __init__(self, session: str | None = None, section: int | None = None):
        super().__init__()
        self.source = "local"
        self.session = validate_session(session or os.getenv("MODULE10_DEMO_SESSION") or uuid4().hex[:12])
        self.persist = session is not None or bool(os.getenv("MODULE10_DEMO_SESSION"))
        self.run_id = uuid4().hex
        self.section = section or 0
        self.version = build_version()
        self.records: list[dict] = []
        self._lock = Lock()
        self._log_provider = None
        self._logger = None
        self._exporters = []
        self._recording_failed = False
        self.export_status = "disabled"
        endpoint = os.getenv("MODULE10_OTLP_ENDPOINT", "").rstrip("/")
        if endpoint:
            self._configure(endpoint)

    def _configure(self, endpoint: str):
        if urlsplit(endpoint).scheme not in ("http", "https"):
            raise ValueError("MODULE10_OTLP_ENDPOINT must be an HTTP(S) collector base URL.")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        resource = Resource.create({
            "service.name": "devops-companion",
            "service.version": self.version,
            "deployment.environment.name": os.getenv("MODULE10_DEPLOYMENT_ENVIRONMENT", "module10-local"),
        })
        owner = self

        class CheckedExporter:
            def __init__(self, delegate):
                self.delegate = delegate
                self.failed = False

            def export(self, batch):
                try:
                    result = self.delegate.export(batch)
                    if result.name != "SUCCESS":
                        self.failed = True
                        owner.export_status = "failed"
                        print("Telemetry export failed; check the local collector.", file=sys.stderr)
                    return result
                except Exception:
                    self.failed = True
                    owner.export_status = "failed"
                    # No exception text: transport errors can contain endpoints/tokens.
                    print("Telemetry export failed; check the local collector.", file=sys.stderr)
                    raise

            def shutdown(self):
                self.delegate.shutdown()

        trace_exporter = CheckedExporter(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=3))
        log_exporter = CheckedExporter(OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=3))
        self._exporters = [trace_exporter, log_exporter]
        self._provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
        self._provider.add_span_processor(BatchSpanProcessor(trace_exporter, schedule_delay_millis=1000))
        self._tracer = self._provider.get_tracer("module10.exposure")
        self._log_provider = LoggerProvider(resource=resource)
        self._log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter, schedule_delay_millis=1000))
        self._logger = self._log_provider.get_logger("module10.exposure")
        self.source = "opentelemetry"
        self.export_status = "pending"

    @contextmanager
    def transaction(self, request):
        attributes = {
            "service.name": "devops-companion", "service.version": self.version,
            "demo.session.id": self.session, "demo.run.id": self.run_id,
            "demo.section": self.section, "request.id": request.request_id,
            "user.id": request.user_id or "unspecified", "team.id": request.team,
        }
        with self.request(attributes=attributes) as trace:
            token = CURRENT.set(trace)
            try:
                yield trace
            except Exception as exc:
                trace.mark_error(type(exc).__name__)
                self.complete(trace, {"status": "failed"}, 500)
                raise
            finally:
                CURRENT.reset(token)

    def release(self, trace, release, behavior):
        values = {
            "agent.behavior": behavior["name"],
            # A scalar field cannot also parent another field in Logz.io's index.
            "agent.behavior_version": behavior["version"],
            "feature_flag.key": release["flag_key"],
            "feature_flag.version": str(release["version"]),
            "feature_flag.result.value": release["recovery_planning_enabled"],
            "feature_flag.variation_index": release["variation_index"],
            "feature_flag.provider.name": "LaunchDarkly",
            "feature_flag.context.id": release["context_key"],
            "feature_flag.set.id": self.run_id,
            "launchdarkly.reason": release["reason"].get("kind", "UNKNOWN"),
        }
        for key, value in values.items():
            trace.set_root_attribute(key, value)
        return values

    def complete(self, trace, body: dict, status_code: int):
        calls = body.get("model_calls", [])
        tools = [e["tool"] for e in body.get("events", []) if e["status"] == "completed"]
        record = {
            **trace.attributes, "type": "module10-exposure",
            "event.name": "agent.request.completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace.trace_id, "span_id": trace.span_id,
            "http.response.status_code": status_code, "request.status": body["status"],
            "models.served": body.get("model", {}).get("served", []),
            "portkey.fallback_used": body.get("routing", {}).get("fallback_used", False),
            "portkey.fallback_attempted": body.get("routing", {}).get("fallback_attempted", False),
            "recovery.plan_saved": body.get("recovery_plan") is not None,
            "recovery.operator_review_required": body.get("recovery_plan") is not None,
            "agent.planner_executed": any(c["agent"] == "Recovery Planner" for c in calls),
            "tools.executed": tools,
        }
        scenario = (
            "interrupted" if record["portkey.fallback_used"] else
            "planner" if record["agent.planner_executed"] else "investigation"
        )
        record["demo.scenario"] = f"section-{self.section}:{record['team.id']}:{scenario}"
        if status_code >= 400:
            trace.mark_error("request_failed")
        for key in ("http.response.status_code", "request.status", "portkey.fallback_used",
                    "portkey.fallback_attempted", "recovery.plan_saved", "agent.planner_executed"):
            trace.set_root_attribute(key, record[key])
        if self._logger is not None:
            # Logs carry native OTLP context and explicit correlation fields.
            try:
                self._logger.emit(body="Agent request completed", attributes=record)
            except Exception:
                self._recording_failed = True
                self.export_status = "failed"
                print("Unable to queue telemetry; the agent result is unchanged.", file=sys.stderr)
        with self._lock:
            self.records.append(record)
        self._save(record)

    def _save(self, record):
        if not self.persist:
            return
        try:
            directory = session_directory(self.session)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{record['trace_id']}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({**record, "telemetry.export": self.export_status}, indent=2))
            temporary.replace(path)
        except OSError:
            print("Unable to save the telemetry session manifest.", file=sys.stderr)

    def flush(self):
        if self._provider is None:
            return
        try:
            ok = self._provider.force_flush(timeout_millis=5000)
            ok = self._log_provider.force_flush(timeout_millis=5000) and ok
            self.export_status = (
                "collector-accepted" if ok and not self._recording_failed and not any(e.failed for e in self._exporters) else "failed"
            )
        except Exception:
            self.export_status = "failed"
        for record in self.records:
            self._save(record)
        if self.export_status == "failed":
            print("Telemetry is incomplete; confirm collector delivery before Section 3.", file=sys.stderr)

    def close(self):
        self.flush()
        for provider in (self._provider, self._log_provider):
            if provider is not None:
                provider.shutdown()

    def status(self):
        return f"Telemetry: {self.export_status}. Collector acceptance does not confirm Logz.io ingestion."
