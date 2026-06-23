# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
tests/test_module7_memory.py
=============================
Unit tests for MongoStore and Neo4jStore (backed by mocks).
All tests run with AGENT_MOCK_MEMORY=true via conftest autouse fixture.
"""
import pytest

from module7.memory.embeddings import EmbeddingService
from module7.memory.mongo_store import MongoStore
from module7.memory.neo4j_store import Neo4jStore


# ---------------------------------------------------------------------------
# MongoStore tests
# ---------------------------------------------------------------------------

class TestMongoStore:
    """Tests for MongoStore using MockMongo backend."""

    def setup_method(self):
        self.store = MongoStore()
        self.embeddings = EmbeddingService()

    def test_store_episodic_memory(self):
        """Upsert to episodic memory_type returns confirmation string with doc ID."""
        vec = self.embeddings.embed("notification-svc degraded")
        result = self.store.upsert(
            "test-ep-001", vec, "episodic",
            "notification-svc degraded",
            {"service_name": "notification-svc"},
        )
        assert isinstance(result, str)
        assert "test-ep-001" in result

    def test_recall_with_metadata_filter(self):
        """Vector search with filter returns records with matching metadata."""
        vec = self.embeddings.embed("degraded service")
        results = self.store.vector_search(vec, "episodic", {"severity": "degraded"}, 5)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert "id" in r
            assert "score" in r
            assert "content" in r

    def test_memory_type_isolation(self):
        """Records upserted to episodic do not appear in procedural queries."""
        vec = self.embeddings.embed("test isolation")
        unique_id = "isolation-test-ep-999"
        self.store.upsert(unique_id, vec, "episodic", "isolation test", {})

        # Query procedural — should not contain the episodic ID
        proc_results = self.store.vector_search(vec, "procedural", None, 10)
        proc_ids = [r["id"] for r in proc_results]
        assert unique_id not in proc_ids

    def test_upsert_deduplication(self):
        """Upserting the same ID twice results in exactly one record with that ID."""
        vec = self.embeddings.embed("dedup test")
        vid = "dedup-test-001"
        self.store.upsert(vid, vec, "episodic", "first", {})
        self.store.upsert(vid, vec, "episodic", "second", {})  # overwrite

        results = self.store.vector_search(vec, "episodic", None, 20)
        matching = [r for r in results if r["id"] == vid]
        assert len(matching) == 1

    def test_round_trip(self):
        """Upsert then query with same vector returns upserted ID as top match."""
        vec = self.embeddings.embed("round trip test")
        vid = "round-trip-001"
        self.store.upsert(vid, vec, "episodic", "round trip", {})

        results = self.store.vector_search(vec, "episodic", None, 5)
        assert len(results) > 0
        assert results[0]["id"] == vid

    def test_query_unknown_type_returns_empty(self):
        """Querying an unknown memory_type returns an empty list."""
        vec = self.embeddings.embed("test")
        results = self.store.vector_search(vec, "nonexistent_type", None, 5)
        assert results == []

    def test_query_procedural_returns_resolution_patterns(self):
        """Procedural memory_type returns records with resolution_pattern metadata."""
        vec = self.embeddings.embed("how to fix ECS")
        results = self.store.vector_search(vec, "procedural", None, 5)
        assert len(results) >= 2
        for r in results:
            assert "resolution_pattern" in r.get("metadata", {})

    def test_query_consolidated_returns_facts(self):
        """Consolidated memory_type returns records with fact metadata."""
        vec = self.embeddings.embed("what do we know")
        results = self.store.vector_search(vec, "consolidated", None, 5)
        assert len(results) >= 2
        for r in results:
            assert "fact" in r.get("metadata", {})


# ---------------------------------------------------------------------------
# Neo4jStore tests
# ---------------------------------------------------------------------------

class TestNeo4jStore:
    """Tests for Neo4jStore using MockNeo4j backend."""

    def setup_method(self):
        self.store = Neo4jStore()

    def test_create_service_node(self):
        """create_node returns a confirmation string."""
        result = self.store.create_node("Service", "test-svc", {"env": "prod"})
        assert isinstance(result, str)
        assert "test-svc" in result

    def test_create_relationship(self):
        """create_relationship returns a confirmation string with source and target."""
        result = self.store.create_relationship(
            "notification-svc", "Service",
            "DEPENDS_ON",
            "api-gateway", "Service",
        )
        assert isinstance(result, str)
        assert "notification-svc" in result
        assert "api-gateway" in result

    def test_blast_radius_notification_svc(self):
        """get_blast_radius for notification-svc returns api-gateway and auth-svc."""
        result = self.store.get_blast_radius("notification-svc")
        assert result["service"] == "notification-svc"
        assert "api-gateway" in result["affected_services"]
        assert "auth-svc" in result["affected_services"]

    def test_multi_hop_traversal(self):
        """get_blast_radius with hops=2 returns at least 2 affected services."""
        result = self.store.get_blast_radius("notification-svc", hops=2)
        assert len(result["affected_services"]) >= 2

    def test_blast_radius_unknown_service_returns_empty(self):
        """get_blast_radius for unknown service returns empty affected_services."""
        result = self.store.get_blast_radius("nonexistent-svc")
        assert result["affected_services"] == []
        assert result["service"] == "nonexistent-svc"

    def test_run_cypher_node_lookup(self):
        """run_cypher with node lookup pattern returns node records."""
        results = self.store.run_cypher("MATCH (s:Service) RETURN s")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_run_cypher_relationship_traversal(self):
        """run_cypher with relationship pattern returns relationship records."""
        results = self.store.run_cypher(
            "MATCH (a)-[:DEPENDS_ON]->(b) RETURN a, b"
        )
        assert isinstance(results, list)
        assert len(results) > 0

    def test_run_cypher_path_query(self):
        """run_cypher with path pattern returns path records."""
        results = self.store.run_cypher(
            "MATCH path = (s:Service)-[:DEPENDS_ON*1..2]-(c) RETURN path"
        )
        assert isinstance(results, list)
        assert len(results) > 0
