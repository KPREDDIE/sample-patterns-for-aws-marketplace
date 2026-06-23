# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Domain Adaptation Engine — core data models and engine implementation.

Provides declarative dataclasses for domain configuration and the
PromptBuilder, ToolScoper, and DomainAdapter engine classes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class PromptLayers:
    """Four-layer domain prompt definition."""
    role: str                   # Domain persona and authority
    knowledge_context: str      # What the agent knows and consults
    constraints: str            # What it must/must not do
    communication: str          # Tone, format, length norms


@dataclass
class GuardrailPolicy:
    """Bedrock Guardrails policy configuration for a domain."""
    pii_handling: str = "ANONYMIZE"                                         # BLOCK or ANONYMIZE
    pii_entities: list[str] = field(default_factory=lambda: ["NAME", "EMAIL", "PHONE"])
    denied_topics: list[dict[str, Any]] = field(default_factory=list)
    content_filters: dict[str, str] = field(default_factory=dict)          # category -> strength
    word_filters: list[str] = field(default_factory=list)


@dataclass
class CorpusConfig:
    """Bedrock Knowledge Base corpus configuration."""
    knowledge_base_id: str | None = None
    description: str = ""
    chunking_strategy: str = "HIERARCHICAL"
    embedding_model: str = "amazon.titan-embed-text-v2:0"


_VALID_PII_HANDLING = {"BLOCK", "ANONYMIZE"}
_VALID_FILTER_STRENGTHS = {"NONE", "LOW", "MEDIUM", "HIGH"}
_NAME_PATTERN = re.compile(r"^[a-z_]+$")


@dataclass
class DomainConfig:
    """Declarative domain configuration — the unit of specialization."""
    name: str
    display_name: str
    prompt_layers: PromptLayers
    tool_names: list[str]
    guardrail_policy: GuardrailPolicy
    corpus_config: CorpusConfig | None = None
    evaluation_criteria: dict[str, str] = field(default_factory=dict)
    mock_env_var: str = "AGENT_MOCK_DOMAIN"

    def __post_init__(self) -> None:
        # Requirement 10.1 — name must be non-empty, lowercase letters and underscores only
        if not self.name or not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"DomainConfig.name must be non-empty and match ^[a-z_]+$, got: {self.name!r}"
            )

        # Requirement 10.4 — tool_names must be a non-empty list
        if not self.tool_names:
            raise ValueError("DomainConfig.tool_names must be a non-empty list of strings")

        # Requirement 10.2 — pii_handling must be BLOCK or ANONYMIZE
        if self.guardrail_policy.pii_handling not in _VALID_PII_HANDLING:
            raise ValueError(
                f"GuardrailPolicy.pii_handling must be one of {_VALID_PII_HANDLING}, "
                f"got: {self.guardrail_policy.pii_handling!r}"
            )

        # Requirement 10.3 — content_filters values must be NONE, LOW, MEDIUM, or HIGH
        for category, strength in self.guardrail_policy.content_filters.items():
            if strength not in _VALID_FILTER_STRENGTHS:
                raise ValueError(
                    f"GuardrailPolicy.content_filters[{category!r}] must be one of "
                    f"{_VALID_FILTER_STRENGTHS}, got: {strength!r}"
                )


@dataclass
class DomainAgent:
    """A specialized agent produced by the engine."""
    agent: Runnable
    config: DomainConfig
    guardrail_id: str | None = None
    system_prompt: str = ""


# ---------------------------------------------------------------------------
# Engine Classes
# ---------------------------------------------------------------------------

class PromptBuilder:
    """Composes four PromptLayers into a single system prompt string."""

    _LAYER_HEADERS: dict[str, str] = {
        "role": "## Role\n",
        "knowledge_context": "## Knowledge Context\n",
        "constraints": "## Constraints\n",
        "communication": "## Communication\n",
    }
    _LAYER_ORDER = ["role", "knowledge_context", "constraints", "communication"]

    def compose(self, layers: PromptLayers) -> str:
        """Return a formatted system prompt from the four prompt layers.

        Composes layers in order: role → knowledge_context → constraints → communication.
        Each layer is preceded by a section header and followed by a blank line.
        """
        system_prompt = ""
        for layer_name in self._LAYER_ORDER:
            section_text = getattr(layers, layer_name)
            system_prompt += self._LAYER_HEADERS[layer_name] + section_text + "\n\n"
        return system_prompt


class ToolScoper:
    """Selects tools from a registry by name for a specific domain."""

    def __init__(self, registry: dict[str, BaseTool]):
        self._registry = registry

    def select(self, tool_names: list[str]) -> list[BaseTool]:
        """Return BaseTool instances for the given names, preserving order."""
        tools = []
        for name in tool_names:
            if name not in self._registry:
                raise KeyError(name)
            tools.append(self._registry[name])
        return tools


class DomainAdapter:
    """Takes a base model + DomainConfig and produces a specialized agent."""

    def __init__(self, tool_registry: dict[str, BaseTool] | None = None):
        self._tool_registry: dict[str, BaseTool] = tool_registry or {}

    def register_tools(self, tools: dict[str, BaseTool]) -> None:
        """Add tools to the shared registry."""
        self._tool_registry.update(tools)

    def adapt(self, base_model: Any, config: DomainConfig, checkpointer: Any = None) -> DomainAgent:
        """Produce a specialized DomainAgent from a base model and domain config.

        If ``checkpointer`` is provided (any LangGraph ``BaseCheckpointSaver``),
        it is attached to the compiled agent for session persistence. This lets
        callers add framework-level conversation continuity (e.g. Module 7's
        Redis-backed checkpointer) without changing the adaptation pattern.
        """
        import logging
        logger = logging.getLogger(__name__)

        # Step 1: Compose four-layer system prompt
        system_prompt = PromptBuilder().compose(config.prompt_layers)

        # Step 2: Select domain-scoped tools from registry
        tools = ToolScoper(self._tool_registry).select(config.tool_names)

        # Step 3: Build guardrail (best-effort, non-blocking)
        guardrail_id = None
        try:
            from module5.engine.guardrail_config import build_guardrail
            guardrail_id = build_guardrail(config.guardrail_policy, config.name)
        except Exception:
            logger.warning("Guardrail creation failed, proceeding without guardrails")

        # Step 4: Assemble LangGraph agent (with optional checkpointer)
        from langgraph.prebuilt import create_react_agent
        if checkpointer is not None:
            agent = create_react_agent(
                base_model, tools, prompt=system_prompt, checkpointer=checkpointer
            )
        else:
            agent = create_react_agent(base_model, tools, prompt=system_prompt)

        return DomainAgent(
            agent=agent,
            config=config,
            guardrail_id=guardrail_id,
            system_prompt=system_prompt,
        )
