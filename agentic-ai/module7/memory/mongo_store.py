# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/mongo_store.py
==============================
MongoStore — long-term semantic memory using MongoDB Atlas Vector Search.

Documents are stored in the `memories` collection with an `embedding`
field (1024-dim float array from Bedrock Titan Embeddings v2) and a
`memory_type` field that partitions memory into episodic, procedural,
and consolidated types.

Vector search uses the Atlas `vector_index` index. Metadata filters are
passed as pre-filters to the $vectorSearch aggregation stage.

Delegates to MockMongo when AGENT_MOCK_MEMORY=true.
"""
from __future__ import annotations

import os

from module7.mock.mongo_mock import MockMongo


def _is_mock() -> bool:
    return os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"


def _insecure_tls() -> bool:
    """Whether to skip TLS certificate verification.

    Defaults to False (secure). Set MODULE7_INSECURE_TLS=true ONLY as a
    local-development workaround for machines whose Python cannot validate
    the Atlas certificate chain (a common macOS issue). The real fix is to
    install up-to-date CA certificates (e.g. `pip install --upgrade certifi`
    or run the "Install Certificates.command" bundled with python.org Python)
    and point SSL_CERT_FILE at them. Never enable this in production.
    """
    return os.getenv("MODULE7_INSECURE_TLS", "").lower() == "true"


_TLS_WARNED = False


class MongoStore:
    """
    Manages long-term semantic memory in MongoDB Atlas.

    Parameters
    ----------
    uri : str, optional
        MongoDB connection string. Falls back to MONGODB_URI env var.
    database : str
        Database name. Default 'agent_memory'.
    collection : str
        Collection name. Default 'memories'.
    vector_index : str
        Atlas Vector Search index name. Default 'vector_index'.
    """

    def __init__(
        self,
        uri: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        vector_index: str | None = None,
    ) -> None:
        if _is_mock():
            self._backend = MockMongo()
            self._mock = True
        else:
            from pymongo import MongoClient
            from pymongo.server_api import ServerApi

            _uri = uri or os.getenv("MONGODB_URI")
            client_kwargs: dict = {
                "server_api": ServerApi("1"),
                "serverSelectionTimeoutMS": 10000,
            }
            if _insecure_tls():
                import logging
                global _TLS_WARNED
                if not _TLS_WARNED:
                    logging.getLogger(__name__).warning(
                        "MODULE7_INSECURE_TLS=true — skipping MongoDB TLS certificate "
                        "verification. Local-dev workaround only; do not use in production."
                    )
                    _TLS_WARNED = True
                client_kwargs["tlsInsecure"] = True
            else:
                # Secure default: verify the Atlas certificate chain against
                # certifi's CA bundle. This makes verification work on Python
                # builds with an empty system trust store (common on macOS)
                # without any per-machine certificate installation.
                from module7.config.tls import ensure_secure_ca
                ca_path = ensure_secure_ca()
                if ca_path:
                    client_kwargs["tlsCAFile"] = ca_path
            self._client = MongoClient(_uri, **client_kwargs)
            _db = database or os.getenv("MONGODB_DATABASE", "agent_memory")
            _col = collection or os.getenv("MONGODB_COLLECTION", "memories")
            self._col = self._client[_db][_col]
            self._vector_index = vector_index or os.getenv("MONGODB_VECTOR_INDEX", "vector_index")
            self._mock = False

    def upsert(
        self,
        doc_id: str,
        embedding: list[float],
        memory_type: str,
        content: str,
        metadata: dict,
    ) -> str:
        """
        Upsert a memory document. Uses doc_id as deduplication key.

        Parameters
        ----------
        doc_id : str
            Stable content-derived ID (same content always maps to same ID).
        embedding : list[float]
            1024-dim vector from Titan Embeddings v2.
        memory_type : str
            One of 'episodic', 'procedural', 'consolidated'.
        content : str
            The original text that was embedded.
        metadata : dict
            Arbitrary metadata (service_name, severity, timestamp, etc.).
        """
        if self._mock:
            return self._backend.upsert(doc_id, embedding, memory_type, content, metadata)

        doc = {
            "_id": doc_id,
            "embedding": embedding,
            "memory_type": memory_type,
            "content": content,
            **metadata,
        }
        self._col.replace_one({"_id": doc_id}, doc, upsert=True)
        return f"Stored memory {doc_id} (type={memory_type})"

    def vector_search(
        self,
        embedding: list[float],
        memory_type: str | None = None,
        filter_dict: dict | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """
        Semantic search using Atlas Vector Search ($vectorSearch).

        Parameters
        ----------
        embedding : list[float]
            Query vector.
        memory_type : str | None
            Filter to a specific memory type, or None for all types.
        filter_dict : dict | None
            Additional metadata filters (e.g. {"severity": "degraded"}).
        top_k : int
            Maximum results to return.

        Returns
        -------
        list[dict]
            Each dict has 'id', 'score', 'content', 'memory_type', 'metadata'.
        """
        if self._mock:
            return self._backend.vector_search(embedding, memory_type, filter_dict, top_k)

        # Build the pre-filter for $vectorSearch
        pre_filter: dict = {}
        if memory_type:
            pre_filter["memory_type"] = {"$eq": memory_type}
        if filter_dict:
            for k, v in filter_dict.items():
                pre_filter[k] = {"$eq": v}

        vector_search_stage: dict = {
            "index": self._vector_index,
            "path": "embedding",
            "queryVector": embedding,
            "numCandidates": top_k * 10,
            "limit": top_k,
        }
        if pre_filter:
            vector_search_stage["filter"] = pre_filter

        pipeline = [
            {"$vectorSearch": vector_search_stage},
            {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"embedding": 0}},  # exclude the large vector from results
        ]

        results = []
        for doc in self._col.aggregate(pipeline):
            doc_id = str(doc.pop("_id", ""))
            score = doc.pop("score", 0.0)
            content = doc.pop("content", "")
            mem_type = doc.pop("memory_type", "")
            results.append({
                "id": doc_id,
                "score": score,
                "content": content,
                "memory_type": mem_type,
                "metadata": doc,  # remaining fields are metadata
            })
        return results
