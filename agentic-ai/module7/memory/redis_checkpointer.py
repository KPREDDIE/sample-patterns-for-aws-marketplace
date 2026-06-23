# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/redis_checkpointer.py
======================================
LangGraph BaseCheckpointSaver backed by Redis Cloud.

Persists the full LangGraph checkpoint (message state + metadata) as a
JSON blob under the key `checkpoint:{thread_id}:{checkpoint_id}` with a
24-hour TTL. The latest checkpoint pointer is stored under
`checkpoint:{thread_id}:latest`.

This gives the agent session continuity across invocations without any
tool calls — the framework restores the prior message state automatically
on every turn.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional, Sequence, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

_SESSION_TTL = int(os.getenv("REDIS_SESSION_TTL", str(60 * 60 * 24)))  # 24h


class RedisCheckpointer(BaseCheckpointSaver):
    """
    LangGraph checkpointer that persists session state in Redis Cloud.

    Each LangGraph thread_id maps to a Redis key namespace. The full
    checkpoint (messages + metadata) is serialized to JSON and stored
    with a TTL. Sessions expire automatically — no manual cleanup needed.
    """

    def __init__(self) -> None:
        super().__init__()
        from module7.memory.redis_store import RedisStore
        self._store = RedisStore()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _checkpoint_key(self, thread_id: str, checkpoint_id: str) -> str:
        return f"checkpoint:{thread_id}:{checkpoint_id}"

    def _latest_key(self, thread_id: str) -> str:
        return f"checkpoint:{thread_id}:latest"

    def _write(self, key: str, data: dict) -> None:
        # Encode bytes values as base64 strings for JSON serialization
        serializable = {}
        for k, v in data.items():
            serializable[k] = v.decode("latin-1") if isinstance(v, bytes) else v
        if self._store._mock:
            self._store._backend.set(key, json.dumps(serializable))
        else:
            self._store._client.set(key, json.dumps(serializable), ex=_SESSION_TTL)

    def _read(self, key: str) -> dict | None:
        if self._store._mock:
            raw = self._store._backend.get(key)
        else:
            raw = self._store._client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        # Decode latin-1 strings back to bytes where needed
        decoded = {}
        for k, v in data.items():
            decoded[k] = v.encode("latin-1") if isinstance(v, str) and k in ("checkpoint", "metadata") else v
        return decoded

    # ── BaseCheckpointSaver interface ─────────────────────────────────────

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Return the latest checkpoint for a thread, or None if no history."""
        thread_id = config["configurable"].get("thread_id", "default")
        latest_raw = self._read(self._latest_key(thread_id))
        if not latest_raw:
            return None

        checkpoint_id = latest_raw.get("checkpoint_id")
        if not checkpoint_id:
            return None

        data = self._read(self._checkpoint_key(thread_id, checkpoint_id))
        if not data:
            return None

        checkpoint = self.serde.loads_typed((data["type"], data["checkpoint"]))
        metadata = self.serde.loads_typed((data["meta_type"], data["metadata"]))

        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id,
                                     "checkpoint_id": checkpoint_id}},
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=data.get("parent_config"),
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints — returns only the latest for simplicity."""
        if config is None:
            return
        result = self.get_tuple(config)
        if result:
            yield result

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> RunnableConfig:
        """Persist a checkpoint to Redis."""
        thread_id = config["configurable"].get("thread_id", "default")
        checkpoint_id = checkpoint["id"]

        cp_type, cp_bytes = self.serde.dumps_typed(checkpoint)
        meta_type, meta_bytes = self.serde.dumps_typed(metadata)

        data = {
            "type": cp_type,
            "checkpoint": cp_bytes,
            "meta_type": meta_type,
            "metadata": meta_bytes,
            "parent_config": config.get("configurable", {}).get("checkpoint_id"),
        }

        self._write(self._checkpoint_key(thread_id, checkpoint_id), data)
        self._write(self._latest_key(thread_id), {"checkpoint_id": checkpoint_id})

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """Store intermediate writes — no-op for this implementation."""
        pass
