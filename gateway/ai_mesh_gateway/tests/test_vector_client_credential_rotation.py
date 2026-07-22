"""Credential rotation / provider switching: _resolve_vector_client must build a
FRESH client from the CURRENT per-org provider config on every call — never a
cached client carrying a rotated-away key.

VECTOR_PROVIDER_SYNC refreshes its config cache when the Redis hash changes; this
pins that the resolver does not add its own stale client cache on top.
"""
from __future__ import annotations

import pytest

import main


class _Sync:
    """Stub of VECTOR_PROVIDER_SYNC returning a mutable config (simulating a
    rotation/switch that the config-cache refresh has already applied)."""

    def __init__(self, cfg):
        self.cfg = cfg

    def get_provider_config(self, org_id, provider_type):
        if provider_type == self.cfg.get("provider_type", "pinecone"):
            return self.cfg
        return None


def test_pinecone_credential_rotation_reflected(monkeypatch):
    cfg = {"is_active": True, "provider_type": "pinecone", "api_key": "key-v1",
           "embedding_model": "text-embedding-3-small"}
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(cfg))

    client1, ptype = main._resolve_vector_client("pinecone", org_id=1)
    assert ptype == "pinecone"
    assert client1._api_key == "key-v1"

    cfg["api_key"] = "key-v2"  # rotate the org's key
    client2, _ = main._resolve_vector_client("pinecone", org_id=1)
    assert client2._api_key == "key-v2", "rotated key not reflected (stale client cache)"
    assert client2 is not client1, "resolver returned a cached (stale) client instance"


def test_provider_switch_reflected(monkeypatch):
    # Org switches pinecone -> milvus; resolver must return the new provider client.
    # RFC1918 private IP: the SSRF guard permits it (legit self-hosted vector DB)
    # and no DNS resolution is needed for a literal IP.
    cfg = {"is_active": True, "provider_type": "milvus",
           "connection_url": "http://10.0.0.5:19530", "api_key": "tok"}
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(cfg))
    client, ptype = main._resolve_vector_client("milvus", org_id=1)
    assert ptype == "milvus"
    assert client is not None
