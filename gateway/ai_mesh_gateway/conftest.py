"""
Root conftest for all gateway (FastAPI Data Plane) tests.

Provides async fakeredis fixtures, a pre-built InputScanner,
sample auth context payloads, and a mocked LiteLLM completion function.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

# Keep the documented gate self-contained. main.py imports ``ai_mesh_shared`` (which
# lives at repo-root/shared), and the gateway source modules (``scanner``, ``main`` …)
# are imported flat by the test fixtures. Without these on sys.path the documented gate
# ``cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`` errors at
# FIXTURE SETUP with ``ModuleNotFoundError: No module named 'ai_mesh_shared'`` unless a
# manual PYTHONPATH is supplied (module-level collection passes because the fixtures
# import main lazily). Mirror tests/leakhunt/conftest.py so the gate runs from a clean
# shell. conftest is imported before any fixture/test module runs.
_CONFTEST_DIR = Path(__file__).resolve().parent  # gateway/ai_mesh_gateway
_SHARED_SRC = _CONFTEST_DIR.parents[1] / "shared"  # repo-root/shared (ai_mesh_shared pkg)
for _p in (str(_CONFTEST_DIR), str(_SHARED_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _reset_bedrock_client_singleton():
    """Task 2: isolate the process-wide Bedrock boto3 client between tests."""
    from ai_mesh_gateway.bedrock_client import reset_bedrock_client_for_tests

    reset_bedrock_client_for_tests()
    yield
    reset_bedrock_client_for_tests()


@pytest.fixture(autouse=True)
def _hermetic_tier2_provider(monkeypatch):
    """Host `.env` may set TIER2_PROVIDER=gemini + GOOGLE_API_KEY.

    Pytest must never construct a live Gemini client or call generateContent.
    Tests that exercise Gemini set TIER2_PROVIDER explicitly (after this fixture).
    """
    monkeypatch.setenv("TIER2_PROVIDER", "bedrock")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    reset_fn = None
    try:
        from bedrock_scanner import reset_tier2_factory_for_tests as reset_fn
    except ImportError:
        try:
            from ai_mesh_gateway.bedrock_scanner import reset_tier2_factory_for_tests as reset_fn
        except ImportError:
            reset_fn = None
    if reset_fn is not None:
        reset_fn()



@pytest.fixture()
def fake_redis_server():
    """Shared in-memory Redis server for all fixtures in a test."""
    return fakeredis.FakeServer()


@pytest_asyncio.fixture()
async def fake_redis(fake_redis_server):
    """Async fakeredis client (decode_responses=True)."""
    client = fakeredis.aioredis.FakeRedis(
        server=fake_redis_server,
        decode_responses=True,
    )
    yield client
    await client.aclose()


@pytest.fixture()
def fake_sync_redis(fake_redis_server):
    """Synchronous fakeredis client for setup/verification."""
    return fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)


@pytest.fixture()
def input_scanner():
    """InputScanner instance with a small thread pool for tests."""
    from scanner import InputScanner

    return InputScanner(thread_pool_size=2)


@pytest.fixture()
def sample_auth_payload():
    """Valid Redis payload for a GatewayAPIKey, as stored in auth:apikey:{hash}."""
    return {
        "key_id": "550e8400-e29b-41d4-a716-446655440000",
        "prefix": "zs_test_",
        "user_id": 1,
        "project_id": "proj-test-001",
        "permissions": {
            "allowed_actions": ["chat", "completion", "embedding"],
            "denied_actions": [],
        },
        "allowed_models": ["gpt-4o-mini"],
        "rate_limit_tpm": 50000,
        "risk_score": 0.0,
        "is_active": True,
        "expires_at": None,
    }


@pytest.fixture()
def sample_disabled_payload(sample_auth_payload):
    """Auth payload for a disabled key."""
    return {**sample_auth_payload, "is_active": False}


@pytest.fixture()
def sample_expired_payload(sample_auth_payload):
    """Auth payload for an expired key."""
    return {**sample_auth_payload, "expires_at": "2020-01-01T00:00:00+00:00"}


@pytest.fixture()
def mock_litellm_response():
    """Canned non-streaming LiteLLM response dict."""
    return {
        "id": "chatcmpl-test-001",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you today?",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18,
        },
    }


@pytest.fixture()
def sample_compiled_policies():
    """Compiled policy bundle as stored in policies:compiled Redis key."""
    return {
        "compiled_at": 1700000000.0,
        "version": 1,
        "policy_count": 1,
        "policies": [
            {
                "policy": {
                    "id": 1,
                    "name": "Block Injection",
                    "code": "INJECT_BLOCK",
                    "category": "Security",
                    "severity": "CRITICAL",
                    "description": "Block prompt injection attempts",
                    "enabled": True,
                    "priority": 10,
                    "metadata": {},
                    "version": 1,
                },
                "rules": [
                    {
                        "id": 1,
                        "name": "Ignore instructions",
                        "rule_type": "regex",
                        "condition": {
                            "regex": r"ignore\s+previous\s+instructions",
                            "field": "prompt",
                        },
                        "action": "block",
                        "redaction_config": {},
                        "priority": 10,
                        "enabled": True,
                        "description": "",
                    }
                ],
            }
        ],
    }


class MockAsyncIterator:
    """Async iterator for simulating streaming LiteLLM responses."""

    def __init__(self, chunks: List[Any]):
        self._chunks = list(chunks)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


@pytest.fixture()
def streaming_chunks():
    """Canned streaming LiteLLM response chunks as MagicMock objects."""
    chunks = []
    for i, token in enumerate(["Hello", " world", "!"]):
        chunk = MagicMock()
        chunk.model_dump.return_value = {
            "id": "chatcmpl-stream-001",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None if i < 2 else "stop",
                }
            ],
        }
        chunks.append(chunk)
    return chunks


@pytest.fixture()
def router_config():
    """Base gateway config dict for LLMRouter initialization (Redis-backed models)."""
    return {
        "org_only_inference": True,
        "upstream_llm_url": "",
        "litellm_default_model": "gpt-4o-mini",
        "litellm_drop_params": True,
        "litellm_request_timeout": 30,
        "litellm_num_retries": 1,
        "litellm_fallback_models": None,
    }


@pytest.fixture()
def sample_chat_body():
    """Standard OpenAI-compatible chat completion request body."""
    return {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }


@pytest.fixture()
def sample_auth_payload_with_governance(sample_auth_payload):
    """Auth payload with governance fields (context tokens, MCP)."""
    return {
        **sample_auth_payload,
        "max_context_tokens": 4096,
        "mcp_allowed_tools": ["calculator", "search"],
        "mcp_max_tool_calls": 10,
    }
