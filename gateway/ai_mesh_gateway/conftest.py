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

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio
import yaml


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
def sample_yaml_config(tmp_path):
    """Write a temporary litellm_config.yaml and return its path."""
    config = {
        "model_list": [
            {
                "model_name": "gpt-4o-mini",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-test-key",
                },
            },
            {
                "model_name": "claude-3-haiku",
                "litellm_params": {
                    "model": "anthropic/claude-3-haiku-20240307",
                    "api_key": "sk-ant-test",
                },
            },
            {
                "model_name": "local-llama",
                "litellm_params": {
                    "model": "ollama/llama3",
                    "api_base": "http://localhost:11434",
                },
            },
        ],
        "litellm_settings": {
            "drop_params": True,
        },
    }
    yaml_file = tmp_path / "litellm_config.yaml"
    yaml_file.write_text(yaml.dump(config))
    return str(yaml_file)


@pytest.fixture()
def router_config():
    """Base gateway config dict for LLMRouter initialization (no YAML)."""
    return {
        "litellm_config_path": "",
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
