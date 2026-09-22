"""The exposed DevOps Companion review endpoint used by the demo."""

from __future__ import annotations

from typing import Any

from .agent import (
    StrandsDependencyUnavailable,
    create_strands_agent,
    extract_agent_text,
    system_prompt_for,
)
from .control_planes import LaunchController, ModelGateway
from .models import LaunchDecision, ReviewRequest, ReviewResponse, new_id
from .telemetry import TraceRecorder, TraceSession


class ExposureService:
    """Connect release targeting, model routing, and endpoint telemetry."""

    def __init__(
        self,
        launch_controller: LaunchController,
        model_gateway: ModelGateway,
        telemetry: TraceRecorder,
        *,
        require_strands: bool = False,
    ) -> None:
        self.launch_controller = launch_controller
        self.model_gateway = model_gateway
        self.telemetry = telemetry
        self.require_strands = require_strands
        self.last_trace_id: str | None = None

    def review(self, request: ReviewRequest) -> ReviewResponse:
        trace_id = new_id("trace")
        with self.telemetry.request(
            trace_id=trace_id,
            attributes={
                "request.id": request.request_id,
                "tenant.id": request.tenant_id,
                "endpoint": "/invoke",
            },
        ) as trace:
            self.last_trace_id = trace.trace_id
            with trace.span(
                "launchdarkly.evaluate",
                {"tenant.id": request.tenant_id},
            ) as flag_span:
                decision = self.launch_controller.evaluate(request.tenant_id)
                flag_span.set_attribute("release.variation", decision.variation)
                flag_span.set_attribute("agent.version", decision.agent_version)
                flag_span.set_attribute("release.source", decision.source)

            trace.set_root_attribute("release.variation", decision.variation)
            trace.set_root_attribute("agent.version", decision.agent_version)

            if not decision.enabled:
                with trace.span(
                    "response.serialize",
                    {"http.status_code": 503, "release.disabled": True},
                ):
                    body = {
                        "status": "disabled",
                        "message": (
                            "The DevOps Companion release is temporarily "
                            "disabled by its operational kill switch."
                        ),
                        "request_id": request.request_id,
                        "trace_id": trace.trace_id,
                        "tenant_id": request.tenant_id,
                        "release": decision.to_dict(),
                    }
                return ReviewResponse(status_code=503, body=body)

            with trace.span(
                "agent.prepare",
                {
                    "agent.version": decision.agent_version,
                    "prompt.style": decision.prompt_style,
                },
            ) as agent_span:
                try:
                    agent, strands_model = create_strands_agent(
                        self.model_gateway,
                        system_prompt=system_prompt_for(decision.prompt_style),
                    )
                except StrandsDependencyUnavailable:
                    if self.require_strands:
                        raise
                    agent = None
                    strands_model = None
                    agent_span.set_attribute("agent.implementation", "direct-mock")
                else:
                    agent_span.set_attribute("agent.implementation", "strands")

            with trace.span(
                "portkey.gateway",
                {
                    "ai.gateway": "portkey",
                    "requested.alias": getattr(
                        self.model_gateway,
                        "model_alias",
                        "deployment-reviewer",
                    ),
                    "release.variation": decision.variation,
                },
            ) as gateway_span:
                try:
                    if agent is not None:
                        agent_result = agent(
                            request.change_summary,
                            invocation_state={
                                "tenant_id": request.tenant_id,
                                "release_variation": decision.variation,
                                "agent_version": decision.agent_version,
                                "trace_id": trace.trace_id,
                            },
                        )
                        result = strands_model.last_result
                        if result is None:
                            raise RuntimeError(
                                "Strands agent completed without gateway result"
                            )
                        response_text = extract_agent_text(agent_result)
                    else:
                        result = self.model_gateway.complete(
                            messages=[
                                {
                                    "role": "system",
                                    "content": system_prompt_for(
                                        decision.prompt_style
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": request.change_summary,
                                },
                            ],
                            tenant_id=request.tenant_id,
                            release_variation=decision.variation,
                            agent_version=decision.agent_version,
                            trace_id=trace.trace_id,
                        )
                        response_text = result.response_text
                except Exception as exc:
                    gateway_span.mark_error(type(exc).__name__)
                    gateway_span.set_attribute("ai.gateway.fallback", False)
                    gateway_span.set_attribute("ai.gateway.degraded", True)
                    gateway_span.set_attribute("http.status_code", 503)
                    gateway_span.add_event(
                        "model.route.failed",
                        {"failure_type": type(exc).__name__},
                    )
                    with trace.span(
                        "response.serialize",
                        {
                            "http.status_code": 503,
                            "response.degraded": True,
                        },
                    ):
                        return ReviewResponse(
                            status_code=503,
                            body={
                                "status": "degraded",
                                "message": (
                                    "The model gateway could not complete this "
                                    "request. Retry later or use the human "
                                    "support path."
                                ),
                                "request_id": request.request_id,
                                "trace_id": trace.trace_id,
                                "tenant_id": request.tenant_id,
                                "release": decision.to_dict(),
                                "route": {
                                    "requested_alias": getattr(
                                        self.model_gateway,
                                        "model_alias",
                                        "deployment-reviewer",
                                    ),
                                    "status_code": 503,
                                    "degraded": True,
                                    "fallback_used": False,
                                    "attempts": [],
                                    "trace_id": trace.trace_id,
                                },
                            },
                        )
                gateway_span.set_attribute("ai.gateway.fallback", result.fallback_used)
                gateway_span.set_attribute("ai.gateway.degraded", result.degraded)
                gateway_span.set_attribute("gen_ai.response.model", result.actual_model)
                gateway_span.set_attribute("http.status_code", result.status_code)
                if result.attempts:
                    gateway_span.set_attribute(
                        "ai.gateway.primary_status_code",
                        result.attempts[0].status_code,
                    )
                gateway_span.add_event(
                    "model.route.completed",
                    {
                        "primary_target": result.primary_target,
                        "final_target": result.final_target,
                        "attempt_count": len(result.attempts),
                    },
                )

            with trace.span(
                "response.serialize",
                {
                    "http.status_code": result.status_code,
                    "response.degraded": result.degraded,
                },
            ):
                body: dict[str, Any] = {
                    "status": "completed",
                    "message": response_text or result.response_text,
                    "request_id": request.request_id,
                    "trace_id": trace.trace_id,
                    "tenant_id": request.tenant_id,
                    "release": decision.to_dict(),
                    "route": result.public_dict(),
                }
            return ReviewResponse(status_code=result.status_code, body=body)
