# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/config/models.py
=========================
Model configuration for Module 7 Agent Memory Systems.

Mirrors module5/config/models.py and adds get_titan_embedding_model()
for Amazon Bedrock Titan Embeddings v2.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import numpy as np
from langchain_aws import ChatBedrock

# CRIS inference profile IDs
SONNET_4_6 = "us.anthropic.claude-sonnet-4-6"
HAIKU_4_5 = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Titan Embeddings v2
TITAN_EMBED_V2 = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIM = 1024


def _is_mock() -> bool:
    return os.getenv("AGENT_MOCK_MEMORY", "").lower() == "true"


def _resolve_region(region: str | None) -> str:
    return region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def get_chat_bedrock_model(
    region: str | None = None,
    model_id: str = SONNET_4_6,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    streaming: bool = False,
    **kwargs: Any,
) -> ChatBedrock:
    """
    Get a configured ChatBedrock model using CRIS inference profiles.

    Region resolution: explicit region → AWS_REGION → AWS_DEFAULT_REGION → us-east-1.
    """
    aws_region = _resolve_region(region)
    return ChatBedrock(
        model_id=model_id,
        region_name=aws_region,
        model_kwargs={
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        },
        streaming=streaming,
    )


def get_titan_embedding_model(region: str | None = None) -> Callable[[str], list[float]]:
    """
    Return a callable that embeds text to a 1024-dimensional vector.

    In mock mode (AGENT_MOCK_MEMORY=true): returns a deterministic vector
    using numpy.random.default_rng(seed=42) — no Bedrock call.

    In live mode: calls bedrock-runtime invoke_model with Titan Embed v2.
    """
    if _is_mock():
        rng = np.random.default_rng(seed=42)
        _vec: list[float] = rng.random(EMBEDDING_DIM).tolist()
        return lambda text: _vec  # same deterministic vector for all inputs

    import boto3

    aws_region = _resolve_region(region)
    client = boto3.client("bedrock-runtime", region_name=aws_region)

    def embed(text: str) -> list[float]:
        resp = client.invoke_model(
            modelId=TITAN_EMBED_V2,
            body=json.dumps({"inputText": text}),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(resp["body"].read())["embedding"]

    return embed
