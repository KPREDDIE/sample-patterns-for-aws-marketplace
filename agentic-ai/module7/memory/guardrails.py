# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
module7/memory/guardrails.py
============================
Write-path PII anonymization for Module 7 memory.

This is the enforcement point for the ``GuardrailPolicy`` declared on
``MemoryDomainConfig``. Every value written to a memory backend
(MongoDB Atlas, Neo4j) passes through ``anonymize_pii`` first, so raw
personal data never lands in long-term storage.

Two layers:

1. **Deterministic layer (always on).** Regex-based redaction of the
   structured identifiers an agent is most likely to capture from logs,
   tickets, and console output: email addresses, phone numbers, and AWS
   access key IDs. Runs locally with no network call, so it is fast,
   testable offline, and works identically in mock and live mode.

2. **Amazon Bedrock Guardrails layer (production, opt-in).** When
   ``MODULE7_BEDROCK_GUARDRAIL_ID`` is set, content is additionally sent
   through the Bedrock ``ApplyGuardrail`` API, which adds ML-based PII
   detection (e.g. person NAME) on top of the deterministic layer. This
   is the recommended production configuration and is documented in the
   README. It is opt-in so the deterministic layer can be validated
   without provisioning a Bedrock guardrail resource.

The redaction tokens are intentionally human-readable so a presenter or
auditor can see exactly what was removed.
"""
from __future__ import annotations

import os
import re

# Declared structured-PII entity types. Mirrors the entity list on
# MemoryDomainConfig.guardrail_policy.pii_entities for the identifiers
# that are reliably detectable without an ML model.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Phone numbers: require a full 10-digit North-American-style grouping with
# separators so we do not match timestamps (14:32), task counts (3/3),
# latencies (2.3s), or other operational numbers in observations.
_PHONE_RE = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s])\d{3}[-.\s]\d{4}\b"
)

# AWS access key IDs: the documented prefixes (AKIA long-term, ASIA temporary)
# followed by 16 uppercase alphanumerics.
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL_RE, "[REDACTED_EMAIL]"),
    (_AWS_ACCESS_KEY_RE, "[REDACTED_AWS_ACCESS_KEY]"),
    (_PHONE_RE, "[REDACTED_PHONE]"),
]


def anonymize_pii(text: str) -> str:
    """
    Redact structured PII from a string before it is written to memory.

    Removes email addresses, phone numbers, and AWS access key IDs,
    replacing each with a human-readable redaction token. Non-string
    input is returned unchanged.

    When ``MODULE7_BEDROCK_GUARDRAIL_ID`` is set, the deterministic
    result is additionally passed through Amazon Bedrock Guardrails for
    ML-based PII detection (e.g. names).
    """
    if not isinstance(text, str) or not text:
        return text

    for pattern, token in _REDACTIONS:
        text = pattern.sub(token, text)

    guardrail_id = os.getenv("MODULE7_BEDROCK_GUARDRAIL_ID")
    if guardrail_id:
        text = _apply_bedrock_guardrail(text, guardrail_id)

    return text


def anonymize_metadata(metadata: dict | None) -> dict:
    """
    Anonymize the string values of a metadata dict (keys are left intact).

    Non-string values (timestamps, scores, counts) pass through unchanged.
    """
    if not metadata:
        return metadata or {}
    return {
        k: anonymize_pii(v) if isinstance(v, str) else v
        for k, v in metadata.items()
    }


def _apply_bedrock_guardrail(text: str, guardrail_id: str) -> str:
    """
    Run text through Amazon Bedrock Guardrails ApplyGuardrail (production layer).

    Returns the anonymized text on success. On any failure the deterministic
    result is returned unchanged so a guardrail outage never blocks a write
    or loses data — the structured-PII layer has already been applied.
    """
    version = os.getenv("MODULE7_BEDROCK_GUARDRAIL_VERSION", "DRAFT")
    try:
        from module5.engine.guardrail_config import apply_guardrail_check

        result = apply_guardrail_check(guardrail_id, version, text, source="INPUT")
        if result.get("action") == "GUARDRAIL_INTERVENED":
            outputs = result.get("outputs") or []
            if outputs and isinstance(outputs[0], dict):
                return outputs[0].get("text", text)
        return text
    except Exception:
        return text
