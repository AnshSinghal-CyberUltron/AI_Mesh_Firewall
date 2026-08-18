"""Task 2: per-worker singleton BedrockClient + connection pool (no AWS)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ai_mesh_gateway import bedrock_client as bc


def _fake_boto_client(*_args, **_kwargs):
    return MagicMock()


def test_default_bedrock_client_returns_same_instance():
    bc.reset_bedrock_client_for_tests()
    fake = MagicMock()
    with patch.object(bc, "boto3") as boto3:
        boto3.client.return_value = fake
        a = bc.default_bedrock_client()
        b = bc.default_bedrock_client()
    assert a is b
    assert boto3.client.call_count == 1
    bc.reset_bedrock_client_for_tests()


def test_boto_config_sets_pool_from_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", "64")
    bc.reset_bedrock_client_for_tests()
    captured = {}

    def _client(name, config=None, **_kw):
        captured["name"] = name
        captured["config"] = config
        return MagicMock()

    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _client
        bc.default_bedrock_client()
    cfg = captured["config"]
    assert captured["name"] == "bedrock-runtime"
    assert cfg.max_pool_connections == 64
    bc.reset_bedrock_client_for_tests()


def test_boto_retries_mode_standard(monkeypatch):
    monkeypatch.delenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", raising=False)
    bc.reset_bedrock_client_for_tests()
    captured = {}

    def _client(name, config=None, **_kw):
        captured["config"] = config
        return MagicMock()

    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _client
        bc.default_bedrock_client()
    cfg = captured["config"]
    retries = cfg.retries
    mode = retries.get("mode") if isinstance(retries, dict) else getattr(retries, "mode", None)
    attempts = (
        retries.get("max_attempts")
        if isinstance(retries, dict)
        else getattr(retries, "max_attempts", None)
    )
    assert mode == "standard"
    assert mode != "adaptive"
    assert attempts == 2
    bc.reset_bedrock_client_for_tests()


def test_boto_config_derives_pool_from_nofile_when_env_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", raising=False)
    monkeypatch.setattr(bc.resource, "getrlimit", lambda _lim: (400, 1024))
    bc.reset_bedrock_client_for_tests()
    captured = {}

    def _client(name, config=None, **_kw):
        captured["config"] = config
        return MagicMock()

    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _client
        bc.default_bedrock_client()
    cfg = captured["config"]
    assert cfg.max_pool_connections == 100
    assert cfg.max_pool_connections != 10
    bc.reset_bedrock_client_for_tests()


def test_unlimited_nofile_uses_ceiling_not_boto_default(monkeypatch):
    monkeypatch.delenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", raising=False)
    monkeypatch.setattr(bc.resource, "getrlimit", lambda _lim: (-1, -1))
    bc.reset_bedrock_client_for_tests()
    captured = {}

    def _client(name, config=None, **_kw):
        captured["config"] = config
        return MagicMock()

    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _client
        bc.default_bedrock_client()
    cfg = captured["config"]
    assert cfg.max_pool_connections == 10000
    assert cfg.max_pool_connections != 10
    bc.reset_bedrock_client_for_tests()


def test_direct_bedrock_client_is_not_forced_through_singleton(monkeypatch):
    monkeypatch.setenv("GATEWAY_BEDROCK_MAX_POOL_CONNECTIONS", "32")
    bc.reset_bedrock_client_for_tests()
    with patch.object(bc, "boto3") as boto3:
        boto3.client.side_effect = _fake_boto_client
        singleton = bc.default_bedrock_client()
        direct = bc.BedrockClient()
    assert singleton is not direct
    assert boto3.client.call_count == 2
    bc.reset_bedrock_client_for_tests()
