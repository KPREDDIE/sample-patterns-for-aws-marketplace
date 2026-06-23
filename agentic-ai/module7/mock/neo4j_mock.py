# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/mock/neo4j_mock.py
===========================
In-memory Neo4j mock for demo and test use.

Returns deterministic pre-built Cypher result records. Dispatches
run_cypher() responses based on query pattern matching.
"""
from __future__ import annotations


class MockNeo4j:
    """In-memory Neo4j mock. Deterministic pre-built Cypher responses."""

    _BLAST_RADIUS: dict[str, dict] = {
        "notification-svc": {
            "service": "notification-svc",
            "hops": 2,
            "affected_services": ["api-gateway", "auth-svc"],
        },
        "api-gateway": {
            "service": "api-gateway",
            "hops": 2,
            "affected_services": ["auth-svc"],
        },
    }

    _NODE_RECORDS: list[dict] = [
        {"s": {"name": "notification-svc", "type": "Service"}},
        {"s": {"name": "api-gateway", "type": "Service"}},
        {"s": {"name": "auth-svc", "type": "Service"}},
        {"s": {"name": "platform-team", "type": "Team"}},
        {"s": {"name": "notifications-team", "type": "Team"}},
    ]

    _REL_RECORDS: list[dict] = [
        {
            "a": {"name": "notification-svc"},
            "r": "DEPENDS_ON",
            "b": {"name": "api-gateway"},
        },
        {
            "a": {"name": "api-gateway"},
            "r": "DEPENDS_ON",
            "b": {"name": "auth-svc"},
        },
        {
            "a": {"name": "platform-team"},
            "r": "OWNS",
            "b": {"name": "api-gateway"},
        },
        {
            "a": {"name": "platform-team"},
            "r": "OWNS",
            "b": {"name": "auth-svc"},
        },
        {
            "a": {"name": "notifications-team"},
            "r": "OWNS",
            "b": {"name": "notification-svc"},
        },
    ]

    _PATH_RECORDS: list[dict] = [
        {
            "path": [
                "notification-svc",
                "DEPENDS_ON",
                "api-gateway",
                "DEPENDS_ON",
                "auth-svc",
            ]
        },
    ]

    def __init__(self) -> None:
        self._nodes: dict[tuple, dict] = {}
        self._rels: list[dict] = []

    def create_node(
        self,
        node_type: str,
        name: str,
        properties: dict | None = None,
    ) -> str:
        """Create or merge a node (MERGE semantics)."""
        key = (node_type, name)
        self._nodes[key] = {"type": node_type, "name": name, **(properties or {})}
        return f"Created/merged {node_type} node: {name}"

    def create_relationship(
        self,
        source_name: str,
        source_type: str,
        relationship: str,
        target_name: str,
        target_type: str,
        properties: dict | None = None,
    ) -> str:
        """Create or merge a directed relationship."""
        self._rels.append(
            {
                "source": source_name,
                "source_type": source_type,
                "relationship": relationship,
                "target": target_name,
                "target_type": target_type,
                "properties": properties or {},
            }
        )
        return f"Created {source_name} -[{relationship}]-> {target_name}"

    def run_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """Dispatch to pre-built records based on query pattern."""
        q_upper = query.strip().upper()
        if "MATCH PATH" in q_upper or "match path" in query.lower():
            return list(self._PATH_RECORDS)
        elif (
            "-[" in query
            or "-[:" in query
            or "DEPENDS_ON" in q_upper
            or "OWNS" in q_upper
            or "CAUSED_BY" in q_upper
        ):
            return list(self._REL_RECORDS)
        else:
            return list(self._NODE_RECORDS)

    def get_blast_radius(self, service_name: str, hops: int = 2) -> dict:
        """Return pre-built blast radius for known services, empty list for unknown."""
        if service_name in self._BLAST_RADIUS:
            result = dict(self._BLAST_RADIUS[service_name])
            result["hops"] = hops
            return result
        return {"service": service_name, "hops": hops, "affected_services": []}
