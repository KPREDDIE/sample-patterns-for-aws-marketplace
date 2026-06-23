# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/agent.py
=================
Memory-Augmented Agent Factory for Module 7.

Session memory is handled at the framework level: a LangGraph checkpointer
backed by Redis Cloud persists the full message state between invocations.
The agent never calls Redis directly — it simply receives its prior messages
as part of the LangGraph state on every turn.

Long-term semantic memory (MongoDB Atlas) and relationship memory (Neo4j)
are exposed as agent tools that the LLM decides when to call.

Usage
-----
    from module7.agent import create_memory_agent

    agent, session_id = create_memory_agent()

    # First turn
    result = agent.invoke(
        {"messages": [("user", "Is the notification-svc issue resolved?")]},
        config={"configurable": {"thread_id": session_id}},
    )

    # Second turn — agent automatically has the first turn in context
    result = agent.invoke(
        {"messages": [("user", "What was the root cause?")]},
        config={"configurable": {"thread_id": session_id}},
    )
"""
from __future__ import annotations

import uuid

from module7.config.memory_domain import MemoryDomainConfig
from module7.config.models import SONNET_4_6, get_chat_bedrock_model
from module7.tools.memory_tools import MEMORY_TOOLS


def _build_checkpointer():
    """
    Build the default LangGraph checkpointer for session persistence.

    The agent depends only on LangGraph's ``BaseCheckpointSaver`` interface, so
    the session backend is pluggable. This factory picks a sensible default; to
    use a different backend (DynamoDB, Postgres, MongoDB's ``MongoDBSaver``, an
    Amazon Bedrock AgentCore-backed saver, etc.) pass your own instance to
    ``create_memory_agent(checkpointer=...)`` — nothing else changes.

    Selection (env ``MODULE7_CHECKPOINTER``, default ``redis``):
      - ``memory``            → MemorySaver (in-process; also used in mock mode)
      - ``redis``             → official RedisSaver if installed, else the
                                bundled RedisCheckpointer (redis-py)
      - ``none``              → None (no persistence)
    """
    import os

    choice = os.getenv("MODULE7_CHECKPOINTER", "").lower()
    mock = os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"

    if choice == "none":
        return None
    if choice == "memory" or (mock and choice == ""):
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    # Default: Redis (the partner backend showcased in this module).
    # Prefer the official Redis-maintained checkpointer if it is installed.
    try:
        from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import]
        return RedisSaver.from_conn_string(_build_redis_url())
    except ImportError:
        pass

    try:
        from module7.memory.redis_checkpointer import RedisCheckpointer
        return RedisCheckpointer()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            f"Redis checkpointer unavailable ({exc}) — falling back to MemorySaver."
        )
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


def _build_redis_url() -> str:
    """Build a Redis connection URL from environment variables."""
    import os
    host = os.getenv("REDIS_HOST", "localhost")
    port = os.getenv("REDIS_PORT", "6379")
    username = os.getenv("REDIS_USERNAME", "default")
    password = os.getenv("REDIS_PASSWORD", "")
    if password:
        return f"redis://{username}:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


def create_memory_agent(
    base_domain: str = "infrastructure",
    *,
    region: str | None = None,
    verbose: bool = True,
    model_id: str = SONNET_4_6,
    session_id: str | None = None,
    streaming: bool = False,
    checkpointer=None,
) -> tuple:
    """
    Create a memory-augmented agent for Module 7.

    Returns a (compiled_graph, session_id) tuple. The compiled graph is a
    LangGraph StateGraph with a checkpointer for session continuity. Invoke it:

        result = agent.invoke(
            {"messages": [("user", query)]},
            config={"configurable": {"thread_id": session_id}},
        )

    Parameters
    ----------
    base_domain : str
        Base domain to augment with memory (e.g., "infrastructure").
    region : str, optional
        AWS region override.
    verbose : bool
        Print configuration summary to stdout.
    model_id : str
        CRIS inference profile ID. Defaults to Claude Sonnet 4.6.
    session_id : str, optional
        Thread ID for session continuity. Auto-generated UUID if not provided.
    streaming : bool
        Enable token-by-token streaming on the model. Default False.
        Set True for Section 9's live TAO loop demo.
    checkpointer : BaseCheckpointSaver, optional
        Session backend. The agent depends only on LangGraph's
        ``BaseCheckpointSaver`` interface, so any implementation works —
        Redis (default), DynamoDB, Postgres, MongoDB's ``MongoDBSaver``, an
        Amazon Bedrock AgentCore-backed saver, or in-memory. If omitted, a
        default is selected (see ``_build_checkpointer`` / ``MODULE7_CHECKPOINTER``).

    Returns
    -------
    tuple[CompiledGraph, str]
        (agent_graph, session_id)

    Raises
    ------
    ValueError
        If base_domain is invalid.
    """
    from module5.engine.domain_adapter import DomainAdapter

    config = MemoryDomainConfig(base_domain)
    base_model = get_chat_bedrock_model(region=region, model_id=model_id, streaming=streaming)

    # Validate tool names against the registry with a clear error if any is missing
    for name in config.tool_names:
        if name not in MEMORY_TOOLS:
            raise ValueError(
                f"Tool '{name}' listed in MemoryDomainConfig but not found in "
                f"MEMORY_TOOLS registry. Available: {list(MEMORY_TOOLS.keys())}"
            )

    # Build session checkpointer. Any LangGraph BaseCheckpointSaver works —
    # callers may inject DynamoDB/Postgres/MongoDB/AgentCore-backed savers.
    if checkpointer is None:
        checkpointer = _build_checkpointer()

    # Compose memory onto the base model via Module 5's Domain Adaptation Engine.
    # MemoryDomainConfig drives the system prompt, the six memory tools, and the
    # guardrail policy; the Redis checkpointer adds framework-level session
    # continuity. One adapt() call — no changes to the base agent.
    adapter = DomainAdapter(dict(MEMORY_TOOLS))
    domain_agent = adapter.adapt(base_model, config, checkpointer=checkpointer)
    agent_graph = domain_agent.agent

    # Generate or use provided session ID
    sid = session_id or str(uuid.uuid4())

    if verbose:
        import os
        mock = os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"
        cls = type(checkpointer).__name__ if checkpointer is not None else None
        if checkpointer is None:
            checkpointer_type = "none (no persistence)"
        elif cls == "MemorySaver":
            checkpointer_type = "MemorySaver (mock)" if mock else "MemorySaver (in-process)"
        elif cls in ("RedisCheckpointer", "RedisSaver"):
            checkpointer_type = "Redis Cloud"
        else:
            checkpointer_type = cls  # injected backend (e.g. DynamoDBSaver)
        print(f"  [Module 7 Memory Agent] {config.display_name}")
        print(f"  [Domain] {config.name}")
        print(f"  [Model] {model_id}")
        print(f"  [Tools] {len(config.tool_names)}: {', '.join(config.tool_names)}")
        print(f"  [Session checkpointer] {checkpointer_type}")
        print(f"  [Session ID] {sid}")
        print()

    return agent_graph, sid
