"""Data models for the Module 10 safe-exposure demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def new_id(prefix: str) -> str:
    """Return a short, readable identifier for terminal output."""
    return f"{prefix}-{uuid4().hex[:10]}"


@dataclass(frozen=True)
class ReviewRequest:
    """Input accepted by the exposed agent endpoint."""

    tenant_id: str
    change_summary: str
    request_id: str = field(default_factory=lambda: new_id("req"))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewRequest":
        tenant_id = str(payload.get("tenant_id", "")).strip()
        change_summary = str(payload.get("change_summary", "")).strip()
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not change_summary:
            raise ValueError("change_summary is required")
        if len(tenant_id) > 128:
            raise ValueError("tenant_id is too long")
        if len(change_summary) > 4000:
            raise ValueError("change_summary is too long")

        request_id = str(payload.get("request_id", "")).strip() or new_id("req")
        if len(request_id) > 128:
            raise ValueError("request_id is too long")
        return cls(
            tenant_id=tenant_id,
            change_summary=change_summary,
            request_id=request_id,
        )


@dataclass(frozen=True)
class LaunchDecision:
    """The release decision returned by the LaunchDarkly adapter."""

    enabled: bool
    variation: str
    agent_version: str
    prompt_style: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "variation": self.variation,
            "agent_version": self.agent_version,
            "prompt_style": self.prompt_style,
            "source": self.source,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GatewayAttempt:
    """One model-routing attempt observed by the application."""

    target: str
    model: str
    status_code: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "target": self.target,
            "model": self.model,
            "status_code": self.status_code,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class ModelResult:
    """Normalized response returned by either the mock or live Portkey adapter."""

    requested_alias: str
    primary_target: str
    final_target: str
    actual_model: str
    status_code: int
    response_text: str
    degraded: bool
    attempts: tuple[GatewayAttempt, ...]
    trace_id: str
    gateway_request_id: str | None = None

    @property
    def fallback_used(self) -> bool:
        return len(self.attempts) > 1 or self.degraded

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_alias": self.requested_alias,
            "primary_target": self.primary_target,
            "final_target": self.final_target,
            "actual_model": self.actual_model,
            "status_code": self.status_code,
            "degraded": self.degraded,
            "fallback_used": self.fallback_used,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "trace_id": self.trace_id,
            "gateway_request_id": self.gateway_request_id,
        }

    def public_dict(self) -> dict[str, Any]:
        """Return route evidence safe to show in the demo response."""
        return {
            "requested_alias": self.requested_alias,
            "primary_target": self.primary_target,
            "final_target": self.final_target,
            "actual_model": self.actual_model,
            "status_code": self.status_code,
            "degraded": self.degraded,
            "fallback_used": self.fallback_used,
            "attempt_count": len(self.attempts),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class ReviewResponse:
    """HTTP response returned by the exposed agent."""

    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class RuntimeStatus:
    """Human-readable status for the live/mock adapters."""

    component: str
    mode: str
    detail: str
