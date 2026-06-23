# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/config/memory_domain.py
================================
MemoryDomainConfig — extends Module 5's DomainConfig to add memory
capabilities to any base agent via the Domain Adaptation Engine.
"""
from __future__ import annotations

from module5.engine.domain_adapter import DomainConfig, GuardrailPolicy
from module7.prompts.system_prompts import build_memory_prompt_layers

_MEMORY_TOOL_NAMES = [
    "store_memory",
    "recall_semantic_memory",
    "store_relationship",
    "query_relationship_graph",
    "get_blast_radius",
    "consolidate_session",
]


class MemoryDomainConfig(DomainConfig):
    """
    Domain configuration that adds persistent memory to any base agent.

    Extends Module 5's DomainConfig with:
    - Memory protocol system prompt (recall before answering, store after observing)
    - Six memory tools scoped via DomainAdapter
    - PII anonymization guardrail (NAME, EMAIL, PHONE, AWS_ACCESS_KEY)
    - Session continuity via Redis-backed LangGraph checkpointer (framework level)
    - Long-term semantic memory via MongoDB Atlas Vector Search
    - Relationship memory via Neo4j Aura graph database

    Parameters
    ----------
    base_domain : str
        The base domain being augmented (e.g., "infrastructure", "repository").
        Must be a non-empty string of lowercase letters and underscores.
    """

    def __init__(self, base_domain: str = "infrastructure") -> None:
        if not base_domain or not isinstance(base_domain, str):
            raise ValueError(
                "base_domain must be a non-empty string, "
                f"got: {base_domain!r}"
            )
        super().__init__(
            name=f"memory_{base_domain}",
            display_name=f"Memory-Augmented {base_domain.title()} Agent",
            prompt_layers=build_memory_prompt_layers(),
            tool_names=list(_MEMORY_TOOL_NAMES),
            guardrail_policy=GuardrailPolicy(
                pii_handling="ANONYMIZE",
                pii_entities=["NAME", "EMAIL", "PHONE", "AWS_ACCESS_KEY"],
            ),
        )
        # Memory-specific metadata (not part of base DomainConfig)
        self.base_domain = base_domain
        self.redis_ttl_hours: int = 24
        self.mongo_memory_types: list[str] = ["episodic", "procedural", "consolidated"]
        self.neo4j_node_types: list[str] = [
            "Service", "Team", "Incident", "Decision", "Deployment"
        ]
