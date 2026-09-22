"""Runtime construction for mock, local, and live Module 10 integrations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .control_planes import (
    IntegrationUnavailable,
    LiveLaunchDarkly,
    LivePortkeyGateway,
    MockLaunchDarkly,
    MockPortkeyGateway,
)
from .exposure import ExposureService
from .models import RuntimeStatus
from .telemetry import TraceRecorder, build_trace_recorder


@dataclass
class RuntimeBundle:
    service: Any
    launch_controller: object
    model_gateway: object
    telemetry: TraceRecorder
    statuses: list[RuntimeStatus]
    mode: str
    live_requested: bool

    def close(self) -> None:
        for component in (self.launch_controller, self.model_gateway, self.telemetry):
            if hasattr(component, "close"):
                component.close()


def _resolve_mode(mode: str | None) -> str:
    requested = (mode or os.getenv("MODULE10_MODE", "")).strip().lower()
    if not requested:
        requested = "mock"
    if requested not in {"mock", "local", "live"}:
        raise ValueError(
            "MODULE10_MODE must be one of: mock, local, live"
        )
    return requested


def _require_strands_for_real_mode(mode: str) -> None:
    if mode == "mock":
        return
    try:
        import strands  # noqa: F401
    except ImportError as exc:
        raise IntegrationUnavailable(
            "install strands-agents to run Module 10 in local or live mode"
        ) from exc


def build_runtime(
    mode: str | None = None, *, flags_path: Path | None = None,
    model_backend: str | None = None,
    session: str | None = None,
    section: int | None = None,
    hosted_identity: dict | None = None,
) -> RuntimeBundle:
    resolved_mode = _resolve_mode(mode)
    requested_live = resolved_mode == "live"
    if resolved_mode == "local":
        from .flags import LocalLaunchDarkly
        from .reviewer import BedrockReviewer, ReviewService
        from .observability import Observations

        backend = model_backend or os.getenv("MODULE10_MODEL_BACKEND", "bedrock")
        if backend not in {"bedrock", "portkey"}:
            raise ValueError("MODULE10_MODEL_BACKEND must be bedrock or portkey")
        launch = LocalLaunchDarkly(flags_path)
        try:
            if hosted_identity is not None:
                from .portkey_remote import RemotePortkeyReviewer
                backend = "portkey"
                reviewer = RemotePortkeyReviewer.from_env(hosted_identity)
            elif backend == "portkey":
                from .portkey_local import PortkeyReviewer

                reviewer = PortkeyReviewer.from_env()
            else:
                reviewer = BedrockReviewer.from_env()
        except Exception:
            launch.close()
            raise
        try:
            telemetry = Observations(session, section)
        except Exception:
            reviewer.close()
            launch.close()
            raise
        return RuntimeBundle(
            service=ReviewService(launch, reviewer, telemetry=telemetry),
            launch_controller=launch,
            model_gateway=reviewer,
            telemetry=telemetry,
            statuses=[
                RuntimeStatus("LaunchDarkly", "local", "Server SDK with reloadable local file; events disabled."),
                *([RuntimeStatus("Portkey", "ecs" if hosted_identity is not None else "local",
                                 "Pinned open-source gateway; agent-role routing and planner fallback.")]
                  if backend == "portkey" else []),
                RuntimeStatus("Amazon Bedrock", "live", (
                    "GPT-5.6 Luna and GPT-5.6 Terra through the Runtime Responses API."
                    if backend == "portkey" else "GPT-5.6 Luna through the Runtime Responses API."
                )),
            ],
            mode="local",
            live_requested=False,
        )
    _require_strands_for_real_mode(resolved_mode)
    statuses: list[RuntimeStatus] = []

    if resolved_mode == "mock":
        launch_controller = MockLaunchDarkly()
        statuses.append(
            RuntimeStatus(
                "LaunchDarkly",
                "mock",
                "Deterministic tenant targeting and kill switch.",
            )
        )
    else:
        sdk_key = os.getenv("LD_SDK_KEY") or os.getenv("LAUNCHDARKLY_SDK_KEY", "")
        launch_controller = LiveLaunchDarkly(
            sdk_key,
            version_flag=os.getenv(
                "LD_AGENT_VERSION_FLAG",
                "module10-reviewer-variant",
            ),
            enabled_flag=os.getenv(
                "LD_AGENT_ENABLED_FLAG",
                "module10-reviewer-enabled",
            ),
        )
        statuses.append(
            RuntimeStatus(
                "LaunchDarkly",
                "live",
                "Server SDK configured for flag evaluation.",
            )
        )

    if resolved_mode == "mock":
        model_gateway = MockPortkeyGateway()
        statuses.append(
            RuntimeStatus(
                "Portkey",
                "mock",
                "Deterministic routing from GPT-5.6 Terra to GPT-5.6 Luna.",
            )
        )
    else:
        api_key = os.getenv("PORTKEY_API_KEY", "").strip()
        config_id = (
            os.getenv("PORTKEY_CONFIG_ID")
            or os.getenv("PORTKEY_CONFIG")
            or ""
        ).strip()
        if not config_id:
            raise IntegrationUnavailable(
                "PORTKEY_CONFIG_ID is required in live mode; "
                "map deployment-reviewer to GPT-5.6 Terra with GPT-5.6 Luna fallback "
                "in the Portkey configuration."
            )
        model_gateway = LivePortkeyGateway(
            api_key,
            virtual_key=os.getenv("PORTKEY_VIRTUAL_KEY") or None,
            config=config_id,
            model_alias=os.getenv(
                "PORTKEY_MODEL_ALIAS",
                "deployment-reviewer",
            ),
            base_url=os.getenv("PORTKEY_BASE_URL") or None,
            primary_target=os.getenv(
                "PORTKEY_PRIMARY_TARGET",
                "portkey-primary",
            ),
            fallback_target=os.getenv(
                "PORTKEY_FALLBACK_TARGET",
                "portkey-fallback",
            ),
            max_completion_tokens=int(
                os.getenv("MODULE10_MAX_COMPLETION_TOKENS", "800")
            ),
            reasoning_effort=(
                os.getenv("MODULE10_REASONING_EFFORT") or None
            ),
        )
        statuses.append(
            RuntimeStatus(
                "Portkey",
                resolved_mode,
                "Portkey gateway configured; physical GPT-5.6 routes remain external.",
            )
        )

    telemetry = build_trace_recorder(resolved_mode != "mock")
    statuses.append(
        RuntimeStatus(
            "Logz.io / OpenTelemetry",
            "live" if telemetry.source == "opentelemetry" else resolved_mode,
            telemetry.status(),
        )
    )

    service = ExposureService(
        launch_controller,
        model_gateway,
        telemetry,
        require_strands=resolved_mode != "mock",
    )
    return RuntimeBundle(
        service=service,
        launch_controller=launch_controller,
        model_gateway=model_gateway,
        telemetry=telemetry,
        statuses=statuses,
        mode=resolved_mode,
        live_requested=requested_live,
    )
