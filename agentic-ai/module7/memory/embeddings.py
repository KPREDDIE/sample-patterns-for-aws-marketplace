# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/embeddings.py
=============================
EmbeddingService — wraps Amazon Bedrock Titan Embeddings v2.

In mock mode (AGENT_MOCK_MEMORY=true): returns a deterministic 1024-dim
vector using numpy seed=42 without calling Bedrock.
"""
from __future__ import annotations

EMBEDDING_DIM = 1024


class EmbeddingService:
    """
    Converts text to 1024-dimensional vectors using Bedrock Titan Embed v2.

    Parameters
    ----------
    region : str, optional
        AWS region override. Falls back to AWS_REGION / AWS_DEFAULT_REGION / us-east-1.
    """

    def __init__(self, region: str | None = None) -> None:
        self._region = region
        self._embed_fn = None  # lazy init on first call

    def _get_fn(self):
        if self._embed_fn is None:
            from module7.config.models import get_titan_embedding_model
            self._embed_fn = get_titan_embedding_model(self._region)
        return self._embed_fn

    def embed(self, text: str) -> list[float]:
        """
        Embed text to a 1024-dimensional float vector.

        Parameters
        ----------
        text : str
            Input text, 1–8192 characters.

        Returns
        -------
        list[float]
            Vector of exactly 1024 floats.

        Raises
        ------
        ValueError
            If text is empty or exceeds 8192 characters.
        RuntimeError
            If the Bedrock embedding call fails.
        """
        if not text:
            raise ValueError("text must be 1–8192 characters, got 0")
        if len(text) > 8192:
            raise ValueError(
                f"text must be 1–8192 characters, got {len(text)}"
            )
        try:
            result = self._get_fn()(text)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        return result
