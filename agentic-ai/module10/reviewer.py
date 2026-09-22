"""An exposed investigation agent with a flag-controlled recovery planner."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from .control_planes import IntegrationUnavailable
from .agent_runner import AgentRun, AgentRunError
from .models import ReviewResponse, new_id
from .recovery import RecoveryEvidence, investigate, save_plan
from .observability import Observations


@dataclass(frozen=True)
class TeamReviewRequest:
    team: str
    change_summary: str
    request_id: str
    user_id: str | None = None

    @classmethod
    def from_http(cls, payload: dict[str, Any], team: str | None, user_id: str | None = None) -> "TeamReviewRequest":
        if not team or not team.strip():
            raise ValueError("X-Demo-Team header is required")
        team = team.strip()
        if len(team) > 128:
            raise ValueError("X-Demo-Team is too long")
        if set(payload) - {"change_summary", "request_id"}:
            raise ValueError("body accepts only change_summary and optional request_id")
        summary = payload.get("change_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("change_summary must be a nonempty string")
        if len(summary) > 4000:
            raise ValueError("change_summary is too long")
        request_id = payload.get("request_id", new_id("req"))
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
            raise ValueError("request_id must be a nonempty string of at most 128 characters")
        if user_id is not None and (not user_id.strip() or len(user_id) > 128):
            raise ValueError("X-Demo-User must contain 1–128 characters")
        return cls(team, summary.strip(), request_id.strip(), user_id.strip() if user_id else None)


class BedrockReviewer:
    def __init__(self, client: Any, model_id: str) -> None:
        self.client = client
        self.model_id = model_id
        self.evidence = RecoveryEvidence()

    @classmethod
    def from_env(cls) -> "BedrockReviewer":
        from openai import OpenAI
        from openai.providers import bedrock

        model_id = os.getenv("MODULE10_BEDROCK_MODEL_ID", "us.openai.gpt-5.6-luna")
        if model_id not in {"us.openai.gpt-5.6-luna", "global.openai.gpt-5.6-luna"}:
            raise IntegrationUnavailable(
                "MODULE10_BEDROCK_MODEL_ID must be a US or global GPT-5.6 Luna inference profile"
            )
        # The provider resolves AWS_REGION / AWS_DEFAULT_REGION / shared config
        # and the normal AWS credential chain, including AWS_PROFILE or roles.
        client = OpenAI(
            provider=bedrock(endpoint="runtime", api_key=None, base_url=None),
            timeout=60.0,
            max_retries=0,
        )
        return cls(client, model_id)

    def review(self, summary: str, planning_enabled: bool, run: AgentRun) -> str:
        return investigate(
            self, self.model_id, summary, planning_enabled, self.evidence, run
        )

    def create_response(self, *, agent, evidence_ready, record, deadline, **params):
        """The direct Bedrock path used by Section 1."""
        return self.client.responses.create(**params)

    def close(self) -> None:
        self.client.close()


class ReviewService:
    def __init__(
        self, launch_controller: Any, reviewer: BedrockReviewer,
        artifact_dir: Path | None = None,
        telemetry: Observations | None = None,
    ) -> None:
        self.launch_controller = launch_controller
        self.reviewer = reviewer
        self.telemetry = telemetry or Observations()
        self.artifact_dir = artifact_dir or Path(
            os.getenv("MODULE10_ARTIFACT_DIR") or Path(__file__).with_name("artifacts")
        )

    def status(self) -> dict:
        return {
            "teams": self.launch_controller.status(),
            "model_id": self.reviewer.model_id,
            "endpoint": str(self.reviewer.client.base_url),
        }

    def review(self, request: TeamReviewRequest) -> ReviewResponse:
        with self.telemetry.transaction(request) as trace:
            response = self._review(request, trace)
            self.telemetry.complete(trace, response.body, response.status_code)
            return response

    def _review(self, request: TeamReviewRequest, trace) -> ReviewResponse:
        from botocore.exceptions import BotoCoreError
        from openai import OpenAIError
        from .recovery import behavior_identity

        # Snapshot once: withdrawing planning affects new requests. An admitted
        # planner may finish its draft; no agent has an infrastructure write tool.
        with trace.span("launchdarkly.evaluate") as decision:
            release = self.launch_controller.evaluate(request.team)
            behavior = behavior_identity(release["recovery_planning_enabled"])
            attributes = self.telemetry.release(trace, release, behavior)
            for key, value in attributes.items():
                decision.set_attribute(key, value)
        planning_enabled = release["recovery_planning_enabled"]
        run = AgentRun()
        body = {
            "request_id": request.request_id,
            "trace_id": trace.trace_id,
            "user_id": request.user_id,
            "behavior": behavior,
            "service_version": self.telemetry.version,
            "team": request.team,
            "release": release,
            "capabilities": {"investigation": True, "recovery_planning": planning_enabled},
            "evidence_source": "local-fictional-fixture",
        }
        try:
            review = self.reviewer.review(request.change_summary, planning_enabled, run)
            with trace.span("recovery.save_plan", {"recovery.plan_present": bool(run.plan_text)}):
                plan = save_plan(self.artifact_dir, run.plan_text) if run.plan_text else None
        except (OpenAIError, BotoCoreError, AgentRunError, OSError) as exc:
            return ReviewResponse(502, {
                **body, **run.evidence(self.reviewer.model_id),
                "status": "failed",
                "error": "Investigation did not complete",
                "detail": str(exc),
            })
        # Report the actual saved-plan outcome, independently of model wording.
        planning_outcome = (
            "Recovery plan created. Awaiting operator review."
            if plan else
            "Investigation complete. No recovery plan generated; "
            "planning is handed to the operator."
        )
        return ReviewResponse(200, {
            **body, **run.evidence(self.reviewer.model_id),
            "status": "completed",
            "review": f"{review.rstrip()}\n\n{planning_outcome}",
            "recovery_plan": plan,
        })
