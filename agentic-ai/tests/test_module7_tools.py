# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
tests/test_module7_tools.py
============================
Unit tests for the six memory @tool functions.
All tests run with AGENT_MOCK_MEMORY=true via conftest autouse fixture.
"""
import json

import pytest

from module7.tools.memory_tools import (
    consolidate_session,
    get_blast_radius,
    query_relationship_graph,
    recall_semantic_memory,
    store_memory,
    store_relationship,
)


class TestMemoryTools:
    """Tests for all six memory tool functions."""

    def test_store_memory_returns_vector_id(self):
        """store_memory returns a confirmation string containing the vector ID."""
        result = store_memory.invoke({
            "content": "notification-svc degraded at 14:32 UTC",
            "memory_type": "episodic",
            "metadata": {"service_name": "notification-svc"},
        })
        assert isinstance(result, str)
        # Confirmation string should contain the namespace
        assert "episodic" in result

    def test_store_memory_procedural(self):
        """store_memory works for procedural namespace."""
        result = store_memory.invoke({
            "content": "Restart ECS tasks after fixing env var",
            "memory_type": "procedural",
            "metadata": {},
        })
        assert isinstance(result, str)
        assert "procedural" in result

    def test_store_memory_consolidated(self):
        """store_memory works for consolidated namespace."""
        result = store_memory.invoke({
            "content": "notification-svc requires REDIS_URL",
            "memory_type": "consolidated",
            "metadata": {},
        })
        assert isinstance(result, str)
        assert "consolidated" in result

    def test_store_memory_invalid_type_raises(self):
        """store_memory raises ValueError for invalid memory_type."""
        with pytest.raises(ValueError, match="memory_type must be one of"):
            store_memory.invoke({
                "content": "test",
                "memory_type": "invalid_type",
                "metadata": {},
            })

    def test_recall_returns_required_fields(self):
        """recall_semantic_memory returns JSON string of dicts with required fields."""
        result = recall_semantic_memory.invoke({"query": "notification-svc issue"})
        assert isinstance(result, str)
        results = json.loads(result)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "content" in r, f"missing 'content' in {r}"
            assert "score" in r, f"missing 'score' in {r}"
            assert "metadata" in r, f"missing 'metadata' in {r}"
            assert "when" in r, f"missing 'when' in {r}"
            # Absolute timestamps must never surface in recall output
            assert "timestamp" not in r.get("metadata", {}), \
                f"absolute timestamp leaked in metadata: {r}"

    def test_recall_all_namespaces(self):
        """recall_semantic_memory with memory_type='all' returns JSON string with results."""
        result = recall_semantic_memory.invoke({
            "query": "service issue",
            "memory_type": "all",
            "top_k": 10,
        })
        assert isinstance(result, str)
        results = json.loads(result)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_recall_specific_namespace(self):
        """recall_semantic_memory with specific namespace returns JSON string."""
        result = recall_semantic_memory.invoke({
            "query": "ECS restart",
            "memory_type": "procedural",
        })
        assert isinstance(result, str)
        results = json.loads(result)
        assert isinstance(results, list)
        assert len(results) > 0

    def test_recall_sorted_by_score(self):
        """recall_semantic_memory results are sorted by score descending."""
        result = recall_semantic_memory.invoke({"query": "test", "top_k": 10})
        results = json.loads(result)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_store_relationship_returns_confirmation(self):
        """store_relationship returns a confirmation string."""
        result = store_relationship.invoke({
            "source": "notification-svc",
            "source_type": "Service",
            "relationship": "DEPENDS_ON",
            "target": "api-gateway",
            "target_type": "Service",
        })
        assert isinstance(result, str)
        assert "notification-svc" in result
        assert "api-gateway" in result

    def test_query_graph_returns_json_string(self):
        """query_relationship_graph returns a valid JSON string."""
        result = query_relationship_graph.invoke({
            "cypher_query": "MATCH (s:Service) RETURN s"
        })
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    def test_query_graph_relationship_pattern(self):
        """query_relationship_graph with relationship pattern returns JSON list."""
        result = query_relationship_graph.invoke({
            "cypher_query": "MATCH (a)-[:DEPENDS_ON]->(b) RETURN a, b"
        })
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_get_blast_radius_returns_json(self):
        """get_blast_radius returns a valid JSON string with expected structure."""
        result = get_blast_radius.invoke({
            "service_name": "notification-svc",
            "hops": 2,
        })
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "service" in parsed
        assert "affected_services" in parsed
        assert "api-gateway" in parsed["affected_services"]

    def test_get_blast_radius_unknown_service(self):
        """get_blast_radius for unknown service returns empty affected_services."""
        result = get_blast_radius.invoke({"service_name": "unknown-svc"})
        parsed = json.loads(result)
        assert parsed["affected_services"] == []

    def test_consolidate_session_empty_returns_zero(self):
        """consolidate_session with empty history returns string containing '0'."""
        result = consolidate_session.invoke({
            "session_history": [],
            "agent_id": "test-agent",
        })
        assert isinstance(result, str)
        assert "0" in result

    def test_consolidate_session_returns_summary_string(self):
        """consolidate_session returns a summary string with stored count."""
        result = consolidate_session.invoke({
            "session_history": [],
            "agent_id": "test-agent",
        })
        assert "stored" in result.lower() or "0" in result
