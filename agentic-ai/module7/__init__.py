# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Module 7: Agent Memory Systems

Public API:
    create_memory_agent  — factory returning (compiled_graph, session_id)
    MemoryDomainConfig   — DomainConfig subclass for memory augmentation

Session memory is handled at the framework level via a LangGraph
checkpointer backed by Redis Cloud. Long-term semantic memory uses
MongoDB Atlas Vector Search. Relationship memory uses Neo4j Aura.
"""
from module7.agent import create_memory_agent
from module7.config.memory_domain import MemoryDomainConfig

__all__ = ["create_memory_agent", "MemoryDomainConfig"]
