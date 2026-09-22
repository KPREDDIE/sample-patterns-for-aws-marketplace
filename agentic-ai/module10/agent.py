"""Strands agent layer for the Module 10 deployment reviewer.

The application owns the agent behavior. Portkey owns the model route.
This module deliberately does not know an AWS profile, account, region, or
physical provider model identifier.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from .control_planes import ModelGateway
from .models import ModelResult

try:
    from strands.models.model import Model as _StrandsModel
except ImportError:  # pragma: no cover - optional dependency in mock-only installs
    class _StrandsModel:  # type: ignore[no-redef]
        """Import-time fallback so mock mode can still load without Strands."""

        pass


STABLE_SYSTEM_PROMPT = """You are DevOps Companion reviewer-v1.

Review the proposed deployment change and return a concise, factual risk
summary with one clear recommendation. Do not claim to have inspected systems
that were not supplied in the request. Call out missing evidence explicitly.
"""


CANDIDATE_SYSTEM_PROMPT = """You are DevOps Companion reviewer-v2.

Review the proposed deployment change as a release engineer. Return a
structured deployment-risk review containing:

1. summary
2. severity
3. evidence present and evidence missing
4. recommendation
5. next action

Be factual, concise, and do not claim to have inspected systems that were not
supplied in the request.
"""


class StrandsDependencyUnavailable(RuntimeError):
    """Raised when a non-mock runtime cannot load the Strands SDK."""


def system_prompt_for(style: str) -> str:
    """Return the prompt selected by the release control plane."""

    return (
        CANDIDATE_SYSTEM_PROMPT
        if style == "structured"
        else STABLE_SYSTEM_PROMPT
    )


def _message_text(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert Strands content blocks into the gateway's simple message shape."""

    converted: list[dict[str, str]] = []
    for message in messages:
        parts: list[str] = []
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        if parts:
            converted.append(
                {
                    "role": str(message.get("role", "user")),
                    "content": "\n".join(parts),
                }
            )
    return converted


class PortkeyStrandsModel(_StrandsModel):
    """Minimal Strands model adapter backed by the Module 10 gateway protocol.

    The adapter emits one complete assistant message as a Strands stream. This
    is sufficient for the purpose-built reviewer, which does not use tools or
    multi-step model calls. It also keeps the Portkey attempt metadata available
    to the exposure service after the Strands event loop completes.
    """

    stateful = False

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway
        self.config: dict[str, Any] = {
            "model_id": getattr(gateway, "model_alias", "deployment-reviewer"),
            "params": {"max_completion_tokens": 800},
        }
        self.last_result: ModelResult | None = None

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return dict(self.config)

    def structured_output(
        self,
        output_model: type[Any],
        prompt: Any,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        del output_model, prompt, system_prompt, kwargs

        async def unsupported() -> AsyncGenerator[dict[str, Any], None]:
            raise NotImplementedError(
                "Module 10 uses normal text output; structured output is not configured."
            )
            yield {}

        return unsupported()

    async def stream(
        self,
        messages: Any,
        tool_specs: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: Any = None,
        system_prompt_content: Any = None,
        invocation_state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        del tool_specs, tool_choice, system_prompt_content, kwargs

        state = invocation_state or {}
        gateway_messages: list[dict[str, str]] = []
        if system_prompt:
            gateway_messages.append(
                {"role": "system", "content": system_prompt}
            )
        gateway_messages.extend(_message_text(messages))

        result = self.gateway.complete(
            messages=gateway_messages,
            tenant_id=str(state["tenant_id"]),
            release_variation=str(state["release_variation"]),
            agent_version=str(state["agent_version"]),
            trace_id=str(state["trace_id"]),
        )
        self.last_result = result

        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {
            "contentBlockDelta": {
                "delta": {"text": result.response_text},
            }
        }
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "totalTokens": 0,
                },
                "metrics": {"latencyMs": 0},
            }
        }


def create_strands_agent(
    gateway: ModelGateway,
    *,
    system_prompt: str,
) -> tuple[Any, PortkeyStrandsModel]:
    """Create a fresh stateless Strands agent for one request."""

    try:
        from strands import Agent
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise StrandsDependencyUnavailable(
            "install strands-agents to run Module 10 as a Strands agent"
        ) from exc

    model = PortkeyStrandsModel(gateway)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[],
        callback_handler=None,
    )
    return agent, model


def extract_agent_text(result: Any) -> str:
    """Extract the final assistant text from a Strands AgentResult."""

    message = getattr(result, "message", None)
    if isinstance(message, dict):
        content = message.get("content", [])
        texts = [
            str(block["text"])
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        if texts:
            return "\n".join(texts)
    return str(result)
