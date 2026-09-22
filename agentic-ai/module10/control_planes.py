"""LaunchDarkly and Portkey adapters used by the Module 10 demo.

The mock implementations are deliberately deterministic so the workshop can
show the same release and failover story without external credentials.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

from .models import GatewayAttempt, LaunchDecision, ModelResult


class IntegrationUnavailable(RuntimeError):
    """Raised when a requested live integration is not installed/configured."""


class LaunchController(Protocol):
    source: str

    def evaluate(self, tenant_id: str) -> LaunchDecision:
        ...


class ModelGateway(Protocol):
    source: str
    model_alias: str

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        tenant_id: str,
        release_variation: str,
        agent_version: str,
        trace_id: str,
    ) -> ModelResult:
        ...


@dataclass
class MockLaunchDarkly:
    """Predictable targeting and kill-switch behavior for offline demos."""

    candidate_tenant: str = "platform-beta"
    candidate_enabled: bool = True
    agent_enabled: bool = True
    source: str = "mock"

    def evaluate(self, tenant_id: str) -> LaunchDecision:
        if not self.agent_enabled:
            return LaunchDecision(
                enabled=False,
                variation="disabled",
                agent_version="none",
                prompt_style="none",
                source=self.source,
                reason="devops-review-agent-enabled is false",
            )

        if self.candidate_enabled and tenant_id == self.candidate_tenant:
            return LaunchDecision(
                enabled=True,
                variation="candidate",
                agent_version="reviewer-v2",
                prompt_style="structured",
                source=self.source,
                reason=f"tenant target matched: {self.candidate_tenant}",
            )

        return LaunchDecision(
            enabled=True,
            variation="stable",
            agent_version="reviewer-v1",
            prompt_style="concise",
            source=self.source,
            reason="default stable variation",
        )

    def set_candidate_enabled(self, enabled: bool) -> None:
        self.candidate_enabled = enabled

    def set_agent_enabled(self, enabled: bool) -> None:
        self.agent_enabled = enabled


class LiveLaunchDarkly:
    """Small LaunchDarkly Server SDK adapter.

    The adapter is created only when a LaunchDarkly SDK key is configured.
    Flag names can be changed without touching the demo flow.
    """

    source = "launchdarkly"

    def __init__(
        self,
        sdk_key: str,
        *,
        version_flag: str = "devops-review-agent-version",
        enabled_flag: str = "devops-review-agent-enabled",
    ) -> None:
        try:
            import ldclient
            from ldclient.config import Config
        except ImportError as exc:
            raise IntegrationUnavailable(
                "install launchdarkly-server-sdk to enable LaunchDarkly live mode"
            ) from exc

        if not sdk_key:
            raise IntegrationUnavailable("LD_SDK_KEY is not configured")

        ldclient.set_config(Config(sdk_key))
        self._client = ldclient.get()
        self._version_flag = version_flag
        self._enabled_flag = enabled_flag

    @staticmethod
    def _context(tenant_id: str) -> Any:
        try:
            from ldclient.context import Context

            return Context.builder(tenant_id).kind("tenant").name(tenant_id).build()
        except ImportError:
            from ldclient import User

            return User(tenant_id, custom={"tenant_id": tenant_id})

    def evaluate(self, tenant_id: str) -> LaunchDecision:
        context = self._context(tenant_id)
        enabled = bool(self._client.variation(self._enabled_flag, context, True))
        variation = str(
            self._client.variation(self._version_flag, context, "stable")
        ).lower()
        if not enabled:
            return LaunchDecision(
                enabled=False,
                variation="disabled",
                agent_version="none",
                prompt_style="none",
                source=self.source,
                reason=f"{self._enabled_flag} is false",
            )

        if variation == "candidate":
            return LaunchDecision(
                enabled=True,
                variation="candidate",
                agent_version="reviewer-v2",
                prompt_style="structured",
                source=self.source,
                reason=f"{self._version_flag}=candidate",
            )
        return LaunchDecision(
            enabled=True,
            variation="stable",
            agent_version="reviewer-v1",
            prompt_style="concise",
            source=self.source,
            reason=f"{self._version_flag}=stable",
        )


class MockPortkeyGateway:
    """Portkey-shaped gateway with a one-shot deterministic throttle."""

    source = "mock-portkey"

    def __init__(
        self,
        *,
        model_alias: str = "deployment-reviewer",
        primary_target: str = "portkey-primary",
        fallback_target: str = "portkey-fallback",
        primary_model: str = "gpt-5.6-terra",
        fallback_model: str = "gpt-5.6-luna",
    ) -> None:
        self.model_alias = model_alias
        self.primary_target = primary_target
        self.fallback_target = fallback_target
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self._armed_tenants: set[str] = set()
        self._calls: defaultdict[str, int] = defaultdict(int)

    def arm_primary_throttle(self, tenant_id: str) -> None:
        """Make the next request for a tenant receive a simulated 429."""
        self._armed_tenants.add(tenant_id)

    def call_count(self, tenant_id: str) -> int:
        return self._calls[tenant_id]

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        tenant_id: str,
        release_variation: str,
        agent_version: str,
        trace_id: str,
    ) -> ModelResult:
        del messages, release_variation, agent_version
        self._calls[tenant_id] += 1
        if tenant_id in self._armed_tenants:
            self._armed_tenants.remove(tenant_id)
            attempts = (
                GatewayAttempt(
                    target=self.primary_target,
                    model=self.primary_model,
                    status_code=429,
                    error="simulated primary target throttle",
                ),
                GatewayAttempt(
                    target=self.fallback_target,
                    model=self.fallback_model,
                    status_code=200,
                ),
            )
            return ModelResult(
                requested_alias=self.model_alias,
                primary_target=self.primary_target,
                final_target=self.fallback_target,
                actual_model=self.fallback_model,
                status_code=200,
                response_text=(
                    "Review complete in degraded mode: the primary model was "
                    "throttled, so the backup model returned a concise risk summary."
                ),
                degraded=True,
                attempts=attempts,
                trace_id=trace_id,
                gateway_request_id=f"pk-mock-{self._calls[tenant_id]:03d}",
            )

        return ModelResult(
            requested_alias=self.model_alias,
            primary_target=self.primary_target,
            final_target=self.primary_target,
            actual_model=self.primary_model,
            status_code=200,
            response_text=(
                "Review complete: encryption is present, the deployment is "
                "low risk, and the proposed change can proceed to staging."
            ),
            degraded=False,
            attempts=(
                GatewayAttempt(
                    target=self.primary_target,
                    model=self.primary_model,
                    status_code=200,
                ),
            ),
            trace_id=trace_id,
            gateway_request_id=f"pk-mock-{self._calls[tenant_id]:03d}",
        )


class LivePortkeyGateway:
    """Portkey SDK adapter for an OpenAI-compatible chat completion request."""

    source = "portkey"

    def __init__(
        self,
        api_key: str,
        *,
        virtual_key: str | None = None,
        config: str | None = None,
        model_alias: str = "deployment-reviewer",
        primary_target: str = "portkey-primary",
        fallback_target: str = "portkey-fallback",
        base_url: str | None = None,
        max_completion_tokens: int = 800,
        reasoning_effort: str | None = None,
    ) -> None:
        try:
            from portkey_ai import Portkey
        except ImportError as exc:
            raise IntegrationUnavailable(
                "install portkey-ai to enable Portkey live mode"
            ) from exc

        if not api_key:
            raise IntegrationUnavailable("PORTKEY_API_KEY is not configured")

        kwargs: dict[str, Any] = {"api_key": api_key}
        if virtual_key:
            kwargs["virtual_key"] = virtual_key
        if base_url:
            kwargs["base_url"] = base_url
        if config:
            kwargs["config"] = config
        self._client = Portkey(**kwargs)
        self.model_alias = model_alias
        self.primary_target = primary_target
        self.fallback_target = fallback_target
        self.max_completion_tokens = max_completion_tokens
        self.reasoning_effort = reasoning_effort

    @staticmethod
    def _content(response: Any) -> str:
        try:
            return str(response.choices[0].message.content)
        except (AttributeError, IndexError, KeyError, TypeError):
            if isinstance(response, dict):
                choices = response.get("choices") or [{}]
                message = choices[0].get("message", {})
                return str(message.get("content", ""))
            return str(response)

    @staticmethod
    def _model(response: Any, default: str) -> str:
        value = getattr(response, "model", None)
        if value:
            return str(value)
        if isinstance(response, dict) and response.get("model"):
            return str(response["model"])
        return default

    @staticmethod
    def _request_id(response: Any) -> str | None:
        value = getattr(response, "id", None)
        if value:
            return str(value)
        if isinstance(response, dict) and response.get("id"):
            return str(response["id"])
        return None

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        tenant_id: str,
        release_variation: str,
        agent_version: str,
        trace_id: str,
    ) -> ModelResult:
        metadata = {
            "tenant_id": tenant_id,
            "release_variation": release_variation,
            "agent_version": agent_version,
        }
        client = self._client
        if hasattr(client, "with_options"):
            client = client.with_options(
                trace_id=trace_id,
                metadata=metadata,
            )
        request_kwargs: dict[str, Any] = {
            "model": self.model_alias,
            "messages": messages,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if self.reasoning_effort:
            request_kwargs["reasoning_effort"] = self.reasoning_effort
        response = client.chat.completions.create(**request_kwargs)
        actual_model = self._model(response, self.model_alias)
        degraded = self._degraded(response)
        attempts: tuple[GatewayAttempt, ...]
        if degraded:
            attempts = (
                GatewayAttempt(
                    target=self.primary_target,
                    model=self.model_alias,
                    status_code=429,
                    error="primary attempt was handled by Portkey",
                ),
                GatewayAttempt(
                    target=self.fallback_target,
                    model=actual_model,
                    status_code=200,
                ),
            )
        else:
            attempts = (
                GatewayAttempt(
                    target=self.primary_target,
                    model=actual_model,
                    status_code=200,
                ),
            )

        return ModelResult(
            requested_alias=self.model_alias,
            primary_target=self.primary_target,
            final_target=self.fallback_target if degraded else self.primary_target,
            actual_model=actual_model,
            status_code=200,
            response_text=self._content(response),
            degraded=degraded,
            attempts=attempts,
            trace_id=trace_id,
            gateway_request_id=self._request_id(response),
        )

    @staticmethod
    def _degraded(response: Any) -> bool:
        """Read Portkey fallback metadata when the SDK exposes it.

        A physical served model can legitimately differ from the logical model
        alias on a healthy request, so model-name comparison is not a valid
        fallback detector.
        """

        candidates: list[Any] = []
        for attribute in ("metadata", "headers", "response_metadata"):
            value = getattr(response, attribute, None)
            if value is not None:
                candidates.append(value)
        if isinstance(response, dict):
            candidates.extend(
                response.get(key)
                for key in ("metadata", "headers", "response_metadata")
                if response.get(key) is not None
            )

        for candidate in candidates:
            if isinstance(candidate, dict):
                for key in (
                    "fallback_used",
                    "fallback",
                    "x-portkey-fallback",
                    "x-portkey-fallback-used",
                ):
                    value = candidate.get(key)
                    if isinstance(value, bool):
                        return value
                    if str(value).lower() in {"true", "1", "yes"}:
                        return True
        return False
