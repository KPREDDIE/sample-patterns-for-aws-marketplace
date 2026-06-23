# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/redis_store.py
==============================
RedisStore — turn-by-turn session memory using Redis Cloud.

Each conversation session is stored as a JSON list under the key
`session:{session_id}:turns` with an optional TTL (default 24h).

Delegates to MockRedis when AGENT_MOCK_MEMORY=true.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from module7.mock.redis_mock import MockRedis

_SESSION_TTL_SECONDS = int(os.getenv("REDIS_SESSION_TTL", str(60 * 60 * 24)))  # 24h


def _is_mock() -> bool:
    return os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"


class RedisStore:
    """
    Manages turn-by-turn session memory in Redis Cloud.

    Each session is a list of {role, content, timestamp} dicts stored
    as a JSON string. Sessions expire after TTL seconds (default 24h).

    Parameters
    ----------
    host, port, username, password : str
        Redis Cloud connection details. Fall back to REDIS_* env vars.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if _is_mock():
            self._backend = MockRedis()
            self._mock = True
        else:
            import redis as redis_lib
            self._client = redis_lib.Redis(
                host=host or os.getenv("REDIS_HOST", "localhost"),
                port=int(port or os.getenv("REDIS_PORT", "6379")),
                username=username or os.getenv("REDIS_USERNAME", "default"),
                password=password or os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
                socket_connect_timeout=10,
            )
            self._mock = False

    # ── Session operations ────────────────────────────────────────────────

    def append_turn(self, session_id: str, role: str, content: str) -> int:
        """
        Append a conversation turn to a session.

        Returns the new turn count for the session.
        """
        if self._mock:
            return self._backend.append_turn(session_id, role, content)

        key = f"session:{session_id}:turns"
        existing = self._client.get(key)
        turns = json.loads(existing) if existing else []
        turns.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._client.set(key, json.dumps(turns), ex=_SESSION_TTL_SECONDS)
        return len(turns)

    def get_turns(self, session_id: str) -> list[dict]:
        """
        Get all turns for a session.

        Returns an empty list if the session does not exist or has expired.
        """
        if self._mock:
            return self._backend.get_turns(session_id)

        key = f"session:{session_id}:turns"
        existing = self._client.get(key)
        if not existing:
            return []
        return json.loads(existing)

    def clear_session(self, session_id: str) -> None:
        """Delete all turns for a session."""
        if self._mock:
            self._backend.clear_session(session_id)
            return
        self._client.delete(f"session:{session_id}:turns")

    def session_exists(self, session_id: str) -> bool:
        """Return True if the session has any stored turns."""
        if self._mock:
            return len(self._backend.get_turns(session_id)) > 0
        return bool(self._client.exists(f"session:{session_id}:turns"))

    def get_context_string(self, session_id: str, max_turns: int = 10) -> str:
        """
        Return the last N turns as a formatted string for injection into prompts.

        Format: "user: ...\nassistant: ...\n..."
        """
        turns = self.get_turns(session_id)[-max_turns:]
        if not turns:
            return ""
        return "\n".join(f"{t['role']}: {t['content']}" for t in turns)

    def get_checkpoint_info(self, session_id: str) -> dict | None:
        """
        Return metadata about the latest LangGraph checkpoint for a session.

        Returns a dict with 'key' and 'checkpoint_id' if a checkpoint exists,
        or None if no checkpoint has been stored for this session.
        """
        key = f"checkpoint:{session_id}:latest"
        if self._mock:
            raw = self._backend.get(key)
        else:
            raw = self._client.get(key)
        if not raw:
            return None
        import json as _json
        data = _json.loads(raw)
        return {
            "key": key,
            "checkpoint_id": data.get("checkpoint_id", "unknown"),
        }
