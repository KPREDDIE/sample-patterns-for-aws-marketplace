# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/mock/redis_mock.py
===========================
In-memory Redis mock for demo and test use.

Stores session turns in a dict keyed by session_id.
Deterministic — same inputs always produce same outputs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


class MockRedis:
    """In-memory Redis mock for session memory."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    # ── Core Redis operations ─────────────────────────────────────────────

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def delete(self, *keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                deleted += 1
        return deleted

    def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    def expire(self, key: str, seconds: int) -> bool:
        return key in self._store

    # ── Session-specific helpers ──────────────────────────────────────────

    def append_turn(self, session_id: str, role: str, content: str) -> int:
        """Append a conversation turn to a session list."""
        key = f"session:{session_id}:turns"
        existing = self._store.get(key)
        turns = json.loads(existing) if existing else []
        turns.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._store[key] = json.dumps(turns)
        return len(turns)

    def get_turns(self, session_id: str) -> list[dict]:
        """Get all turns for a session."""
        key = f"session:{session_id}:turns"
        existing = self._store.get(key)
        if existing:
            return json.loads(existing)
        return []

    def clear_session(self, session_id: str) -> None:
        """Clear all turns for a session."""
        key = f"session:{session_id}:turns"
        self._store.pop(key, None)
