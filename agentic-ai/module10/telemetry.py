"""OpenTelemetry-compatible tracing with a deterministic terminal recorder."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import uuid4


def _trace_id() -> str:
    return uuid4().hex


def _safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


@dataclass
class SpanRecord:
    name: str
    attributes: dict[str, Any]
    parent: str | None = None
    status: str = "ok"
    duration_ms: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_span_id: str | None = None
    start_time_ns: int = 0
    end_time_ns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parent": self.parent,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "attributes": dict(self.attributes),
            "events": list(self.events),
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time_ns": self.start_time_ns,
            "end_time_ns": self.end_time_ns,
        }


class SpanCapture:
    def __init__(
        self,
        session: "TraceSession",
        name: str,
        attributes: dict[str, Any],
        start_time_ns: int | None = None,
        end_time_ns: int | None = None,
    ) -> None:
        self.session = session
        self.name = name
        self.record = SpanRecord(
            name=name,
            attributes=_safe_attributes(attributes),
            parent=session.current_parent,
        )
        self._started = 0.0
        self._otel_cm: Any = None
        self._otel_span: Any = None
        self._start_time_ns = start_time_ns
        self._end_time_ns = end_time_ns

    def __enter__(self) -> "SpanCapture":
        self._started = time.perf_counter()
        self.record.start_time_ns = self._start_time_ns or time.time_ns()
        self.record.parent_span_id = self.session._ids[-1]
        self.session._stack.append(self.name)
        if self.session.recorder._tracer is not None:
            from opentelemetry.trace import SpanKind
            self._otel_cm = self.session.recorder._tracer.start_as_current_span(
                self.name,
                attributes=self.record.attributes,
                start_time=self.record.start_time_ns,
                end_on_exit=False,
                kind=SpanKind.CLIENT if self.name == "portkey.attempt" else SpanKind.INTERNAL,
                record_exception=False,
                set_status_on_exception=False,
            )
            self._otel_span = self._otel_cm.__enter__()
            self.record.span_id = format(self._otel_span.get_span_context().span_id, "016x")
        self.session._ids.append(self.record.span_id)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.record.end_time_ns = self._end_time_ns or time.time_ns()
        self.record.duration_ms = (self.record.end_time_ns - self.record.start_time_ns) / 1e6
        if exc is not None:
            self.mark_error(type(exc).__name__)
        if self._otel_cm is not None:
            self._otel_span.end(end_time=self.record.end_time_ns)
            self._otel_cm.__exit__(exc_type, exc, tb)
        if self.session._stack:
            self.session._stack.pop()
        self.session._ids.pop()
        self.session.spans.append(self.record)

    def set_attribute(self, key: str, value: Any) -> None:
        if value is None:
            return
        self.record.attributes[key] = value
        if self._otel_span is not None:
            try:
                self._otel_span.set_attribute(key, value)
            except Exception:
                pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        event = {"name": name}
        if attributes:
            event["attributes"] = _safe_attributes(attributes)
        self.record.events.append(event)
        if self._otel_span is not None:
            try:
                self._otel_span.add_event(name, attributes=event.get("attributes"))
            except Exception:
                pass

    def mark_error(self, error_type: str) -> None:
        self.record.status = "error"
        self.add_event("error", {"type": error_type})
        if self._otel_span is not None:
            try:
                from opentelemetry.trace import Status, StatusCode

                self._otel_span.set_status(Status(StatusCode.ERROR, error_type))
            except Exception:
                pass


class TraceSession:
    def __init__(
        self,
        recorder: "TraceRecorder",
        name: str,
        attributes: dict[str, Any],
        preferred_trace_id: str | None = None,
    ) -> None:
        self.recorder = recorder
        self.name = name
        self.attributes = _safe_attributes(attributes)
        self.trace_id = preferred_trace_id or _trace_id()
        self.spans: list[SpanRecord] = []
        self._stack: list[str] = []
        self._root_started = 0.0
        self._otel_cm: Any = None
        self._otel_root: Any = None
        self.span_id = uuid4().hex[:16]
        self._ids: list[str] = []
        self._error: str | None = None

    @property
    def current_parent(self) -> str | None:
        return self._stack[-1] if self._stack else self.name

    def __enter__(self) -> "TraceSession":
        self._root_started = time.perf_counter()
        self.start_time_ns = time.time_ns()
        if self.recorder._tracer is not None:
            from opentelemetry.trace import SpanKind
            self._otel_cm = self.recorder._tracer.start_as_current_span(
                self.name,
                attributes=self.attributes,
                kind=SpanKind.SERVER,
                record_exception=False,
                set_status_on_exception=False,
            )
            self._otel_root = self._otel_cm.__enter__()
            try:
                self.trace_id = format(
                    self._otel_root.get_span_context().trace_id,
                    "032x",
                )
                self.span_id = format(self._otel_root.get_span_context().span_id, "016x")
            except Exception:
                pass
        self._stack.append(self.name)
        self._ids.append(self.span_id)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc is not None:
            self.mark_error(type(exc).__name__)
        root = SpanRecord(
            name=self.name,
            attributes=self.attributes,
            parent=None,
            status="error" if self._error else "ok",
            duration_ms=(time.perf_counter() - self._root_started) * 1000,
            span_id=self.span_id,
            start_time_ns=self.start_time_ns,
            end_time_ns=time.time_ns(),
        )
        self.spans.insert(0, root)
        if self._otel_cm is not None:
            self._otel_cm.__exit__(exc_type, exc, tb)
        self._stack.clear()
        self.recorder._traces[self.trace_id] = self.snapshot()
        self.recorder.last_trace_id = self.trace_id

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        *,
        start_time_ns: int | None = None,
        end_time_ns: int | None = None,
    ) -> Iterator[SpanCapture]:
        capture = SpanCapture(self, name, attributes or {}, start_time_ns, end_time_ns)
        with capture:
            yield capture

    def set_root_attribute(self, key: str, value: Any) -> None:
        if value is not None:
            self.attributes[key] = value
            if self._otel_root is not None:
                self._otel_root.set_attribute(key, value)

    def mark_error(self, error_type: str) -> None:
        self._error = error_type
        self.set_root_attribute("error.type", error_type)
        if self._otel_root is not None:
            from opentelemetry.trace import Status, StatusCode
            self._otel_root.set_status(Status(StatusCode.ERROR, error_type))

    def snapshot(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "spans": [span.to_dict() for span in self.spans],
        }


class TraceRecorder:
    """Stores trace evidence and optionally exports the same spans via OTLP."""

    def __init__(
        self,
        *,
        otlp_endpoint: str | None = None,
        otlp_headers: dict[str, str] | None = None,
    ) -> None:
        self.source = "mock"
        self._tracer: Any = None
        self._provider: Any = None
        self._traces: dict[str, dict[str, Any]] = {}
        self.last_trace_id: str | None = None

        if otlp_endpoint:
            self._configure_otel(otlp_endpoint, otlp_headers or {})

    def _configure_otel(self, endpoint: str, headers: dict[str, str]) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            return

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
        )
        self._provider = provider
        self._tracer = provider.get_tracer("module10.safe-exposure")
        self.source = "opentelemetry"

    @contextmanager
    def request(
        self,
        *,
        trace_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[TraceSession]:
        session = TraceSession(
            self,
            "POST /invoke",
            attributes or {},
            preferred_trace_id=trace_id,
        )
        with session:
            yield session

    def get(self, trace_id: str | None) -> dict[str, Any] | None:
        if not trace_id:
            return None
        return self._traces.get(trace_id)

    def status(self) -> str:
        if self.source == "opentelemetry":
            return "OTel spans are being exported to the configured OTLP endpoint."
        return "Deterministic in-memory trace records; no external telemetry credentials."


def build_trace_recorder(live: bool) -> TraceRecorder:
    if not live:
        return TraceRecorder()

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return TraceRecorder()

    raw_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
    headers: dict[str, str] = {}
    for item in raw_headers.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            headers[key.strip()] = value.strip()
    return TraceRecorder(otlp_endpoint=endpoint, otlp_headers=headers)
