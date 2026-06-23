# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
tests/test_module7_guardrails.py
================================
Unit tests for the write-path PII anonymizer.
All tests run with AGENT_MOCK_MEMORY=true via conftest autouse fixture.
"""
import json

from module7.memory.guardrails import anonymize_metadata, anonymize_pii
from module7.tools.memory_tools import recall_semantic_memory, store_memory


class TestAnonymizePII:
    """Deterministic structured-PII redaction."""

    def test_redacts_email(self):
        out = anonymize_pii("page alice@example.com about the outage")
        assert "alice@example.com" not in out
        assert "[REDACTED_EMAIL]" in out

    def test_redacts_phone(self):
        out = anonymize_pii("on-call number is 555-123-4567 for escalation")
        assert "555-123-4567" not in out
        assert "[REDACTED_PHONE]" in out

    def test_redacts_aws_access_key(self):
        out = anonymize_pii("leaked key AKIAIOSFODNN7EXAMPLE in the logs")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "[REDACTED_AWS_ACCESS_KEY]" in out

    def test_preserves_operational_numbers(self):
        """Timestamps, task counts, and latencies must not be mistaken for PII."""
        text = "notification-svc degraded at 14:32, 0/3 ECS tasks, P99 2.3s"
        out = anonymize_pii(text)
        assert out == text

    def test_non_string_passthrough(self):
        assert anonymize_pii(None) is None
        assert anonymize_pii(123) == 123

    def test_anonymize_metadata_strings_only(self):
        meta = {
            "service_name": "notification-svc",
            "owner_email": "bob@corp.io",
            "severity": "degraded",
            "task_count": 3,
        }
        out = anonymize_metadata(meta)
        assert out["owner_email"] == "[REDACTED_EMAIL]"
        assert out["service_name"] == "notification-svc"
        assert out["task_count"] == 3


class TestStoreMemoryEnforcesGuardrail:
    """store_memory must anonymize before persisting."""

    def test_store_memory_anonymizes_content(self):
        store_memory.invoke({
            "content": "incident reported by carol@example.com on notification-svc",
            "memory_type": "episodic",
            "metadata": {"service_name": "notification-svc"},
        })
        results = json.loads(recall_semantic_memory.invoke({
            "query": "incident notification-svc",
            "memory_type": "episodic",
            "top_k": 10,
        }))
        stored = [r for r in results if "carol" in r["content"] or "REDACTED_EMAIL" in r["content"]]
        assert stored, "expected the just-stored record to be retrievable"
        for r in stored:
            assert "carol@example.com" not in r["content"]
