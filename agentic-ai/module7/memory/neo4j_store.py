# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/neo4j_store.py
==============================
Neo4jStore — wrapper around the Neo4j driver for relationship memory.

Delegates to MockNeo4j when AGENT_MOCK_MEMORY=true.
"""
from __future__ import annotations

import os

from module7.mock.neo4j_mock import MockNeo4j


def _is_mock() -> bool:
    return os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"


def _insecure_tls() -> bool:
    """Whether to skip TLS certificate verification (default False/secure).

    Set MODULE7_INSECURE_TLS=true ONLY as a local-dev workaround for machines
    that cannot validate the AuraDB certificate chain. When enabled, a
    neo4j+s:// URI is rewritten to neo4j+ssc:// (same encryption, skips cert
    chain verification). The production fix is to install up-to-date CA
    certificates. Never enable this in production.
    """
    return os.getenv("MODULE7_INSECURE_TLS", "").lower() == "true"


_TLS_WARNED = False


class Neo4jStore:
    """
    Manages service dependency nodes and relationships in Neo4j Aura.

    Parameters
    ----------
    uri : str, optional
        Neo4j connection URI. Falls back to NEO4J_URI env var.
    username : str, optional
        Neo4j username. Falls back to NEO4J_USERNAME env var.
    password : str, optional
        Neo4j password. Falls back to NEO4J_PASSWORD env var.
    """

    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if _is_mock():
            self._backend = MockNeo4j()
            self._mock = True
        else:
            from neo4j import GraphDatabase  # type: ignore[import]

            _uri = uri or os.getenv("NEO4J_URI")
            # AuraDB uses neo4j+s:// (TLS). Some Python installs (commonly on
            # macOS) fail to verify the cert chain. As a local-dev workaround
            # only, MODULE7_INSECURE_TLS=true rewrites to neo4j+ssc:// which
            # uses the same encryption but skips cert chain verification.
            if _uri and _uri.startswith("neo4j+s://") and _insecure_tls():
                import logging
                global _TLS_WARNED
                if not _TLS_WARNED:
                    logging.getLogger(__name__).warning(
                        "MODULE7_INSECURE_TLS=true — skipping Neo4j TLS certificate "
                        "verification (neo4j+ssc). Local-dev workaround only; "
                        "do not use in production."
                    )
                    _TLS_WARNED = True
                _uri = _uri.replace("neo4j+s://", "neo4j+ssc://", 1)
            else:
                # Secure default: the neo4j+s:// scheme enforces full cert-chain
                # verification using OpenSSL's trust store. Point OpenSSL at
                # certifi's CA bundle so verification succeeds on Python builds
                # with an empty system trust store (common on macOS).
                from module7.config.tls import ensure_secure_ca
                ensure_secure_ca()
            _user = username or os.getenv("NEO4J_USERNAME", "neo4j")
            _pass = password or os.getenv("NEO4J_PASSWORD")
            self._driver = GraphDatabase.driver(_uri, auth=(_user, _pass))
            self._mock = False

    def create_node(
        self,
        node_type: str,
        name: str,
        properties: dict | None = None,
    ) -> str:
        """Create or merge a node (MERGE semantics — idempotent)."""
        if self._mock:
            return self._backend.create_node(node_type, name, properties)
        props = {"name": name, **(properties or {})}
        cypher = f"MERGE (n:{node_type} {{name: $name}}) SET n += $props RETURN n"
        self.run_cypher(cypher, {"name": name, "props": props})
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
        """Create or merge a directed relationship (MERGE semantics — idempotent)."""
        if self._mock:
            return self._backend.create_relationship(
                source_name, source_type, relationship,
                target_name, target_type, properties,
            )
        cypher = (
            f"MERGE (a:{source_type} {{name: $src}}) "
            f"MERGE (b:{target_type} {{name: $tgt}}) "
            f"MERGE (a)-[r:{relationship}]->(b) "
            f"SET r += $props RETURN r"
        )
        self.run_cypher(
            cypher,
            {"src": source_name, "tgt": target_name, "props": properties or {}},
        )
        return f"Created {source_name} -[{relationship}]-> {target_name}"

    def run_cypher(self, query: str, params: dict | None = None) -> list[dict]:
        """
        Execute a Cypher query and return result records as dicts.

        Raises
        ------
        ValueError
            If the query is syntactically invalid (contains "invalid" in message).
        """
        if self._mock:
            return self._backend.run_cypher(query, params)
        try:
            with self._driver.session() as session:
                result = session.run(query, parameters=params or {})
                return [dict(record) for record in result]
        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc).lower()
            if "syntaxerror" in exc_type.lower() or "invalid" in exc_msg or "syntax" in exc_msg:
                raise ValueError(
                    f"invalid Cypher query: {query!r} — {exc}"
                ) from exc
            raise

    def get_blast_radius(self, service_name: str, hops: int = 2) -> dict:
        """
        Find all services within N hops via DEPENDS_ON relationships.

        Returns empty affected_services list for non-existent services
        without raising an exception.

        Parameters
        ----------
        service_name : str
            Starting service node name.
        hops : int
            Max traversal depth, clamped to 1–5.
        """
        if self._mock:
            return self._backend.get_blast_radius(service_name, hops)

        hops = max(1, min(hops, 5))
        cypher = (
            f"MATCH path = (s:Service {{name: $name}})"
            f"-[:DEPENDS_ON*1..{hops}]->(connected) "
            f"RETURN DISTINCT connected.name AS name"
        )
        try:
            records = self.run_cypher(cypher, {"name": service_name})
            return {
                "service": service_name,
                "hops": hops,
                "affected_services": [r["name"] for r in records if r.get("name")],
            }
        except Exception:
            return {"service": service_name, "hops": hops, "affected_services": []}
