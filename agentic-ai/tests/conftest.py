# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
tests/conftest.py
==================
Shared pytest fixtures for Module 7 tests.

The autouse fixture enforces AGENT_MOCK_MEMORY=true for every test
and clears the _get_stores() lru_cache to prevent store state from
leaking between tests.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def mock_memory_env(monkeypatch):
    """Enforce mock mode and reset store cache for every test."""
    monkeypatch.setenv("AGENT_MOCK_MEMORY", "true")

    # Clear lru_cache before test so each test gets fresh store instances
    try:
        from module7.tools.memory_tools import _get_stores
        _get_stores.cache_clear()
    except ImportError:
        pass

    yield

    # Clear again after test to avoid state leaking to the next test
    try:
        from module7.tools.memory_tools import _get_stores
        _get_stores.cache_clear()
    except ImportError:
        pass
