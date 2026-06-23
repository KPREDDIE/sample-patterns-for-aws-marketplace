# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/mock/mongo_mock.py
===========================
In-memory MongoDB mock for demo and test use.

Returns deterministic pre-built records for the three memory types
(episodic, procedural, consolidated) stored as MongoDB documents
with a memory_type field.

Timestamps are relative to the current date so the sample data stays
current regardless of when it is run.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone


def _ts(days_ago: int, hour: int = 14, minute: int = 32) -> str:
    """Return an ISO 8601 UTC timestamp N days ago at the given hour:minute."""
    dt = datetime.now(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_id(content: str, memory_type: str) -> str:
    digest = hashlib.sha256(f"{memory_type}:{content}".encode()).hexdigest()[:32]
    return f"mem-{memory_type[:3]}-{digest}"


class MockMongo:
    """In-memory MongoDB mock. Deterministic pre-built query responses."""

    # Three episodic records covering different services and severities:
    #   ep-001 — notification-svc degraded
    #   ep-002 — notification-svc resolved/healthy
    #   ep-003 — api-gateway latency spike (different service + severity)
    @classmethod
    def _build_episodic(cls) -> list[dict]:
        return [
            {
                "_id": "ep-001",
                "memory_type": "episodic",
                "content": "notification-svc degraded: 0/3 ECS tasks running. Missing REDIS_URL environment variable.",
                "score": 0.87,
                "metadata": {
                    "service_name": "notification-svc",
                    "severity": "degraded",
                    "timestamp": _ts(1, 14, 32),
                    "source_module": "module1",
                },
            },
            {
                "_id": "ep-002",
                "memory_type": "episodic",
                "content": "notification-svc healthy: 3/3 ECS tasks running after REDIS_URL env var fix and redeployment.",
                "score": 0.82,
                "metadata": {
                    "service_name": "notification-svc",
                    "severity": "healthy",
                    "timestamp": _ts(1, 15, 45),
                    "source_module": "module1",
                },
            },
            {
                "_id": "ep-003",
                "memory_type": "episodic",
                "content": "api-gateway P99 latency spike to 2.3s during peak traffic. Upstream connection pool exhaustion.",
                "score": 0.71,
                "metadata": {
                    "service_name": "api-gateway",
                    "severity": "warning",
                    "timestamp": _ts(3, 10, 15),
                    "source_module": "module1",
                },
            },
        ]

    @classmethod
    def _build_procedural(cls) -> list[dict]:
        return [
            {
                "_id": "pr-001",
                "memory_type": "procedural",
                "content": "notification-svc outage resolution: missing REDIS_URL env var. Fix: inject env var into ECS task definition environment block, force new deployment. Verified: 3/3 tasks healthy within 4 minutes.",
                "score": 0.91,
                "metadata": {
                    "resolution_pattern": "inject REDIS_URL env var into ECS task definition and redeploy",
                    "service_name": "notification-svc",
                    "timestamp": _ts(1, 15, 45),
                },
            },
            {
                "_id": "pr-002",
                "memory_type": "procedural",
                "content": "Scale ECS desired count to 0 then back to 3 to force task replacement when tasks are stuck in PENDING state.",
                "score": 0.83,
                "metadata": {
                    "resolution_pattern": "scale ECS desired count to 0 then back to 3",
                    "service_name": "notification-svc",
                    "timestamp": _ts(14, 11, 30),
                },
            },
        ]

    @classmethod
    def _build_consolidated(cls) -> list[dict]:
        return [
            {
                "_id": "co-001",
                "memory_type": "consolidated",
                "content": "notification-svc requires REDIS_URL env var to start. Fix: inject via ECS task definition environment block. Verified resolved yesterday.",
                "score": 0.88,
                "metadata": {
                    "fact": "notification-svc requires REDIS_URL env var to start. Resolved yesterday.",
                    "source_session": "session-yesterday",
                    "timestamp": _ts(1, 15, 0),
                    "confidence": 0.95,
                },
            },
            {
                "_id": "co-002",
                "memory_type": "consolidated",
                "content": "platform-team owns api-gateway. notifications-team owns notification-svc.",
                "score": 0.82,
                "metadata": {
                    "fact": "platform-team owns api-gateway. notifications-team owns notification-svc.",
                    "source_session": "session-last-week",
                    "timestamp": _ts(7, 10, 0),
                    "confidence": 0.90,
                },
            },
        ]

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        # Build records at init time so timestamps are fresh on each run
        self._EPISODIC = self._build_episodic()
        self._PROCEDURAL = self._build_procedural()
        self._CONSOLIDATED = self._build_consolidated()

    def upsert(self, doc_id: str, embedding: list[float],
               memory_type: str, content: str, metadata: dict) -> str:
        """Store a document. Uses doc_id as deduplication key."""
        self._store[doc_id] = {
            "_id": doc_id,
            "memory_type": memory_type,
            "content": content,
            "embedding": embedding,
            "metadata": metadata,
        }
        return f"Stored memory {doc_id} (type={memory_type})"

    def vector_search(self, embedding: list[float], memory_type: str | None,
                      filter_dict: dict | None, top_k: int) -> list[dict]:
        """Return pre-built records for known memory types."""
        if memory_type == "episodic":
            base = list(self._EPISODIC)
        elif memory_type == "procedural":
            base = list(self._PROCEDURAL)
        elif memory_type == "consolidated":
            base = list(self._CONSOLIDATED)
        elif memory_type is None:  # all
            base = list(self._EPISODIC) + list(self._PROCEDURAL) + list(self._CONSOLIDATED)
        else:
            base = []

        # Prepend any live-upserted records for this type (score 0.95)
        live = [
            {**v, "score": 0.95}
            for v in self._store.values()
            if memory_type is None or v.get("memory_type") == memory_type
        ]

        results = live + base

        # Apply filter_dict client-side (mock only — live uses Atlas server-side)
        if filter_dict:
            filtered = []
            for r in results:
                meta = r.get("metadata", {})
                if all(meta.get(k) == v for k, v in filter_dict.items()):
                    filtered.append(r)
            results = filtered

        # Sort by score descending, deduplicate by _id, normalize to live format
        seen: set[str] = set()
        deduped = []
        for r in sorted(results, key=lambda x: x.get("score", 0), reverse=True):
            rid = str(r.get("_id", ""))
            if rid not in seen:
                seen.add(rid)
                meta = dict(r.get("metadata", {}))
                deduped.append({
                    "id": rid,
                    "score": r.get("score", 0.0),
                    "content": r.get("content", ""),
                    "memory_type": r.get("memory_type", ""),
                    "metadata": meta,
                })

        return deduped[:top_k]
