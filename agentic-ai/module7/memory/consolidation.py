# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/consolidation.py
================================
ConsolidationService — extracts durable facts from session history
and stores them in MongoDB Atlas (memory_type='consolidated') and Neo4j.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

_CONSOLIDATION_PROMPT = """\
Analyze this conversation and extract durable knowledge.

Return a JSON object with exactly these keys:
- "facts": list of strings (user preferences, resolved issues, decisions made)
- "relationships": list of objects, each with string keys "subject", "predicate", "object"
  (new service dependencies or ownership relationships discovered)
- "confidence": float 0.0-1.0 for overall extraction confidence

Conversation:
{history}

Return only valid JSON, no markdown fences."""


@dataclass
class ConsolidationResult:
    """Result of a consolidation operation."""
    facts: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    stored_count: int = 0


class ConsolidationService:
    """
    Extracts durable facts from session history and stores them in
    MongoDB Atlas (memory_type='consolidated') and Neo4j.

    Parameters
    ----------
    mongo_store : MongoStore
    neo4j_store : Neo4jStore
    embedding_service : EmbeddingService
    """

    def __init__(self, mongo_store, neo4j_store, embedding_service) -> None:
        self._mongo = mongo_store
        self._neo4j = neo4j_store
        self._embeddings = embedding_service

    def consolidate(
        self,
        session_history: list[dict],
        agent_id: str,
    ) -> ConsolidationResult:
        """
        Extract and store durable knowledge from a session history.

        Parameters
        ----------
        session_history : list[dict]
            List of message dicts with 'role' and 'content' keys.
            Returns immediately with stored_count=0 if empty.
        agent_id : str
            Identifier for the agent/user session.

        Returns
        -------
        ConsolidationResult
            facts, relationships, and stored_count.

        Raises
        ------
        Exception
            Propagates Bedrock LLM exceptions without partial writes.
        """
        if not session_history:
            return ConsolidationResult(stored_count=0)

        from module7.config.models import get_chat_bedrock_model
        from module7.memory.guardrails import anonymize_pii

        model = get_chat_bedrock_model()
        history_text = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in session_history
        )
        # Write-path guardrail: anonymize PII in the session history before it
        # reaches the consolidation model and before any extracted fact is stored.
        history_text = anonymize_pii(history_text)
        prompt = _CONSOLIDATION_PROMPT.format(history=history_text)

        # Raises on Bedrock failure — propagated without partial writes (Req 6.7)
        response = model.invoke([("user", prompt)])

        # Strip markdown fences if model wraps in ```json ... ```
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        parsed = json.loads(content)
        facts: list[str] = [anonymize_pii(f) for f in parsed.get("facts", [])]
        relationships: list[dict] = parsed.get("relationships", [])
        confidence: float = float(parsed.get("confidence", 0.8))
        timestamp: str = datetime.now(timezone.utc).isoformat()
        stored = 0

        # Store facts in MongoDB memory_type='consolidated'
        for fact in facts:
            vec = self._embeddings.embed(fact)
            from module7.tools.memory_tools import _stable_id
            doc_id = _stable_id(fact, "consolidated")
            self._mongo.upsert(
                doc_id,
                vec,
                "consolidated",
                fact,
                {
                    "agent_id": agent_id,
                    "timestamp": timestamp,
                    "source": "consolidation",
                    "confidence": confidence,
                },
            )
            stored += 1

        # Store relationships in Neo4j — skip failures (Req 6.4)
        for rel in relationships:
            try:
                self._neo4j.create_relationship(
                    rel["subject"],
                    "Service",
                    rel["predicate"],
                    rel["object"],
                    "Service",
                )
                stored += 1
            except Exception:
                continue

        return ConsolidationResult(
            facts=facts,
            relationships=relationships,
            stored_count=stored,
        )
