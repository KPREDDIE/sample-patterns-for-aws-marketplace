"""A bounded Responses tool loop shared by the Companion and recovery planner."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable
from uuid import uuid4

from .observability import span, model_turn, fingerprint


class AgentRunError(RuntimeError):
    """The model did not complete the required, permitted workflow."""


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[str], dict]

    def definition(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"service_name": {"type": "string"}},
                "required": ["service_name"],
                "additionalProperties": False,
            },
        }


@dataclass
class AgentRun:
    events: list[dict] = field(default_factory=list)
    responses: list[dict] = field(default_factory=list)
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    plan_text: str | None = None

    def evidence(self, model_id: str) -> dict:
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for response in self.responses:
            if response["usage"] is None:
                usage = None
                break
            for key in usage:
                usage[key] += response["usage"].get(key, 0)
        return {
            "events": self.events,
            "model": {
                "requested": model_id,
                "served": list(dict.fromkeys(r["model"] for r in self.responses if r["model"])),
            },
            "model_calls": self.responses,
            "usage": usage if self.responses else None,
            **({
                "routing": {
                    "fallback_used": any(r.get("fallback_used", False) for r in self.responses),
                    "fallback_attempted": any(len(r.get("attempts", [])) > 1 for r in self.responses),
                    "gateway": next((r["gateway"] for r in self.responses if r.get("gateway")), "portkey-local"),
                },
            } if any("attempts" in r for r in self.responses) else {}),
        }


def run_agent(client: Any, model_id: str, **kwargs) -> str:
    with span("agent.execute", {
        "agent.role": kwargs["name"],
        "agent.tools.offered": ",".join(tool.name for tool in kwargs["tools"]),
        "agent.definition.version": fingerprint({
            "instructions": kwargs["instructions"],
            "tools": [t.definition() for t in kwargs["tools"]],
        }),
    }):
        return _run_agent(client, model_id, **kwargs)


def _create_response(client, record, **params):
    with model_turn(record):
        response = client.create_response(record=record, **params)
        record.update({
            "response_id": response.id, "model": response.model,
            "status": response.status,
            "usage": response.usage.model_dump() if response.usage else None,
        })
        return response


def _run_agent(
    client: Any,
    model_id: str,
    *,
    name: str,
    instructions: str,
    task: str,
    tools: list[Tool],
    run: AgentRun,
    required_tools: set[str],
) -> str:
    """Execute only advertised tools; retain all output items for stateless turns."""
    handlers = {tool.name: tool for tool in tools}
    completed: set[str] = set()
    history: list[Any] = [{"role": "user", "content": task}]

    for _ in range(5):
        remaining = run.deadline - time.monotonic()
        if remaining <= 0:
            raise AgentRunError("The investigation exceeded its time budget")
        record = {
            "agent": name, "response_id": None, "model": None,
            "status": "failed", "usage": None,
            "call_id": uuid4().hex, "requested_model": model_id,
        }
        run.responses.append(record)
        response = _create_response(
            client,
            agent=name,
            evidence_ready=required_tools.issubset(completed),
            record=record,
            deadline=run.deadline,
            model=model_id,
            instructions=instructions,
            input=history,
            tools=[tool.definition() for tool in tools],
            reasoning={"effort": "none"},
            max_output_tokens=650,
            store=False,
            timeout=min(60.0, remaining),
        )
        if response.status != "completed":
            raise AgentRunError(f"{name} returned {response.status} output")

        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            if not response.output_text.strip():
                raise AgentRunError(f"{name} returned no text")
            if not required_tools.issubset(completed):
                raise AgentRunError(f"{name} finished without the required evidence checks")
            return response.output_text

        # Include reasoning items as well as tool calls. No previous_response_id
        # or server-side conversation storage is required.
        history.extend(item.model_dump(mode="json", exclude_none=True) for item in response.output)
        for call in calls:
            event = {"agent": name, "tool": call.name, "status": "started"}
            run.events.append(event)
            try:
                if call.name not in handlers:
                    raise AgentRunError(f"{name} requested unavailable capability: {call.name}")
                if call.name in completed:
                    raise AgentRunError(f"{name} attempted to repeat {call.name}")
                try:
                    arguments = json.loads(call.arguments)
                except (ValueError, TypeError) as exc:
                    raise AgentRunError(f"Invalid arguments for {call.name}") from exc
                if (
                    not isinstance(arguments, dict)
                    or set(arguments) != {"service_name"}
                    or not isinstance(arguments["service_name"], str)
                ):
                    raise AgentRunError(f"Invalid arguments for {call.name}")
                with span("tool.execute", {
                    "agent.role": name, "tool.name": call.name, "tool.call_id": call.call_id,
                    "agent.delegation": call.name == "delegate_recovery_planning",
                }):
                    result = handlers[call.name].handler(arguments["service_name"])
            except Exception:
                event["status"] = "failed"
                raise
            completed.add(call.name)
            event["status"] = "completed"
            event["summary"] = result.get("summary", "Completed")
            history.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": json.dumps(result),
            })
    raise AgentRunError(f"{name} exceeded its model-turn limit")
