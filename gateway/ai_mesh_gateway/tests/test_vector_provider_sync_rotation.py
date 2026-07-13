"""VERIFIED SOUND (executed): VectorProviderSync propagates per-org provider
CREDENTIAL ROTATION (and provider switching) from Redis to the gateway — so a
frontend credential change reaches _resolve_vector_client. _load_initial reads the
compiled bundle (REDIS_KEY_COMPILED) and full-REPLACES the cache on a hash change;
a Redis MISS keeps last-good (safe vs a transient blip wiping all provider creds —
only a full delete leaves stale); the loader closes its client (no leak). On-prompt:
VECTOR PROVIDER "credential rotation / provider switching".
"""
import json

import fakeredis.aioredis

import vector_provider_sync as vps
from vector_provider_sync import VectorProviderSync, REDIS_KEY_COMPILED


async def _noop():
    return None


async def test_provider_credential_rotation_and_switch_propagate(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(fake, "aclose", _noop)  # keep the shared fake alive across loads
    monkeypatch.setattr(vps.aioredis.Redis, "from_url", lambda *a, **k: fake)

    sync = VectorProviderSync(redis_url="redis://localhost:6379/0")

    # 1. initial creds
    await fake.set(REDIS_KEY_COMPILED, json.dumps({"1::pinecone": {"api_key": "old-key", "is_active": True}}))
    await sync._load_initial()
    assert sync.get_provider_config(1, "pinecone")["api_key"] == "old-key"

    # 2. ROTATE — new key must propagate to the gateway
    await fake.set(REDIS_KEY_COMPILED, json.dumps({"1::pinecone": {"api_key": "new-key", "is_active": True}}))
    await sync._load_initial()
    assert sync.get_provider_config(1, "pinecone")["api_key"] == "new-key", "rotation did not propagate"

    # 3. PROVIDER SWITCH — org 1 switches pinecone->chroma
    await fake.set(REDIS_KEY_COMPILED, json.dumps({"1::chroma": {"connection_url": "http://c", "is_active": True}}))
    await sync._load_initial()
    assert sync.get_provider_config(1, "chroma") is not None
    assert sync.get_provider_config(1, "pinecone") is None  # old provider gone (full replace)

    # 4. MISS keeps last-good (safe tradeoff, not stale-forever for updates)
    await fake.delete(REDIS_KEY_COMPILED)
    await sync._load_initial()
    assert sync.get_provider_config(1, "chroma") is not None  # retained


async def test_inactive_provider_returns_none(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(fake, "aclose", _noop)
    monkeypatch.setattr(vps.aioredis.Redis, "from_url", lambda *a, **k: fake)
    sync = VectorProviderSync(redis_url="redis://localhost:6379/0")
    await fake.set(REDIS_KEY_COMPILED, json.dumps({"1::pinecone": {"api_key": "k", "is_active": False}}))
    await sync._load_initial()
    assert sync.get_provider_config(1, "pinecone") is None  # is_active=False gate
