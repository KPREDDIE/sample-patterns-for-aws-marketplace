# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/tools/memory_tools.py
==============================
Six LangGraph @tool definitions for the memory-augmented agent.

Session memory is handled at the framework level via a LangGraph
checkpointer backed by Redis — the agent never calls Redis directly.

Semantic memory (long-term)  → MongoDB Atlas Vector Search
Relationship memory          → Neo4j Aura

Store instances are lazily initialized and cached via @lru_cache.
Tests clear the cache via _get_stores.cache_clear() in conftest.py.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache

from langchain_core.tools import tool

from module7.memory.guardrails import anonymize_metadata, anonymize_pii

VALID_MEMORY_TYPES = {"episodic", "procedural", "consolidated"}


def _relative_when(ts: str) -> str:
    """Render an ISO timestamp as a human-friendly relative phrase.

    Conversational agents read more naturally when they refer to events in
    relative terms ("yesterday", "earlier this week") rather than as absolute
    timestamps. Comparison is by calendar date, so "yesterday" means the
    previous calendar day regardless of the time of day.
    """
    if not ts or not isinstance(ts, str):
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc).date() - dt.date()).days
    except Exception:
        return ""
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "earlier this week"
    if days < 14:
        return "last week"
    if days < 31:
        return "a couple of weeks ago"
    return "a while ago"


def _stable_id(content: str, memory_type: str) -> str:
    """Derive a stable document ID from content + type for deduplication."""
    digest = hashlib.sha256(f"{memory_type}:{content}".encode()).hexdigest()[:32]
    return f"mem-{memory_type[:3]}-{digest}"


@lru_cache(maxsize=1)
def _get_stores():
    """Lazily initialize and cache all store instances."""
    from module7.memory.consolidation import ConsolidationService
    from module7.memory.embeddings import EmbeddingService
    from module7.memory.mongo_store import MongoStore
    from module7.memory.neo4j_store import Neo4jStore

    mongo = MongoStore()
    neo4j = Neo4jStore()
    embeddings = EmbeddingService()
    consolidation = ConsolidationService(mongo, neo4j, embeddings)
    return mongo, neo4j, embeddings, consolidation


# ---------------------------------------------------------------------------
# Semantic memory tools (MongoDB Atlas)
# ---------------------------------------------------------------------------

@tool
def store_memory(content: str, memory_type: str, metadata: dict) -> str:
    """Store a long-term memory in MongoDB Atlas.

    memory_type must be 'episodic', 'procedural', or 'consolidated'.
    Same content always maps to the same document ID — repeated stores overwrite.
    """
    if memory_type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"memory_type must be one of {sorted(VALID_MEMORY_TYPES)}, got {memory_type!r}"
        )
    # Write-path guardrail: anonymize PII before anything reaches the backend.
    content = anonymize_pii(content)
    metadata = anonymize_metadata(metadata)
    mongo, _, embeddings, _ = _get_stores()
    embedding = embeddings.embed(content)
    doc_id = _stable_id(content, memory_type)
    return mongo.upsert(doc_id, embedding, memory_type, content, metadata)


@tool
def recall_semantic_memory(
    query: str,
    memory_type: str = "all",
    filters: dict = None,
    top_k: int = 5,
) -> str:
    """Recall long-term memories from MongoDB Atlas using semantic similarity.

    memory_type: 'episodic' | 'procedural' | 'consolidated' | 'all' (default)
    filters: metadata filter dict (e.g. {"severity": "degraded"})
    top_k: number of results (1-20)
    Returns a JSON string of matching memory records.
    """
    top_k = max(1, min(top_k, 20))
    mongo, _, embeddings, _ = _get_stores()
    embedding = embeddings.embed(query)

    mt = None if memory_type == "all" else memory_type
    results = mongo.vector_search(embedding, mt, filters, top_k)

    formatted = []
    for r in results:
        meta = {k: v for k, v in r.get("metadata", {}).items() if k != "embedding"}
        when = _relative_when(meta.get("timestamp", ""))
        # Surface a relative phrase (e.g. "yesterday") and drop the raw
        # timestamp — it reads more naturally in agent responses.
        meta.pop("timestamp", None)
        formatted.append({
            "content": r.get("content", ""),
            "score": round(r.get("score", 0.0), 4),
            "memory_type": r.get("memory_type", ""),
            "metadata": meta,
            "when": when,
        })
    return json.dumps(formatted, default=str)


# ---------------------------------------------------------------------------
# Relationship memory tools (Neo4j)
# ---------------------------------------------------------------------------

@tool
def store_relationship(
    source: str,
    source_type: str,
    relationship: str,
    target: str,
    target_type: str,
    properties: dict = None,
) -> str:
    """Store a directed relationship in the Neo4j knowledge graph."""
    _, neo4j, _, _ = _get_stores()
    return neo4j.create_relationship(
        source, source_type, relationship, target, target_type, properties
    )


@tool
def query_relationship_graph(cypher_query: str) -> str:
    """Execute a Cypher query against the Neo4j knowledge graph. Returns JSON string.

    Production note: this tool runs agent-generated Cypher. Bind it to a
    Neo4j role with read-only (and least-privilege) permissions so a
    malformed or adversarial query cannot mutate or delete graph data.
    Write paths go through store_relationship, which uses parameterized MERGE.
    """
    _, neo4j, _, _ = _get_stores()
    try:
        results = neo4j.run_cypher(cypher_query)
        return json.dumps(results, default=str)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def get_blast_radius(service_name: str, hops: int = 2) -> str:
    """Find all services within N hops of the given service in the dependency graph."""
    hops = max(1, min(hops, 5))
    _, neo4j, _, _ = _get_stores()
    return json.dumps(neo4j.get_blast_radius(service_name, hops), default=str)


# ---------------------------------------------------------------------------
# Consolidation tool (Bedrock → MongoDB + Neo4j)
# ---------------------------------------------------------------------------

@tool
def consolidate_session(session_history: list, agent_id: str = "default") -> str:
    """Extract durable facts from session history and store in long-term memory."""
    _, _, _, consolidation = _get_stores()
    result = consolidation.consolidate(session_history, agent_id)
    return (
        f"Consolidated session: stored {len(result.facts)} facts and "
        f"{len(result.relationships)} relationships ({result.stored_count} total)."
    )


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

MEMORY_TOOLS: dict = {
    "store_memory":             store_memory,
    "recall_semantic_memory":   recall_semantic_memory,
    "store_relationship":       store_relationship,
    "query_relationship_graph": query_relationship_graph,
    "get_blast_radius":         get_blast_radius,
    "consolidate_session":      consolidate_session,
}
