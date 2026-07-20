"""VERIFIED SOUND (executed): the config CACHE layer propagates per-org config
UPDATES from Redis to the gateway — so a frontend "disable a feature" reaches
runtime (the prompt's core: "disabled feature MUST NOT execute"). ConfigSync._refresh
full-replaces the per-org entry (`{**self._config, **data}`) on a present key, and
deliberately KEEPS the last-good config on a Redis MISS (safety vs a transient blip
wiping an org's firewall — a documented tradeoff that only affects full config
DELETION, not feature toggles which write the key with the new value).
"""
import json

import fakeredis.aioredis

from config_sync import ConfigSync, REDIS_KEY_PREFIX


async def test_config_update_propagates_and_stale_on_delete_is_safe():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cs = ConfigSync(redis_url="redis://localhost:6379/0", config={"rag_enabled": True})
    key = f"{REDIS_KEY_PREFIX}acme"

    # 1. initial per-org config
    await r.set(key, json.dumps({"rag_enabled": True, "prompt_injection_threshold": 0.8}))
    await cs._refresh(r, org_slug="acme")
    assert cs.get_config("acme")["rag_enabled"] is True

    # 2. UPDATE — org DISABLES rag in the frontend → must reach runtime
    await r.set(key, json.dumps({"rag_enabled": False, "prompt_injection_threshold": 0.8}))
    await cs._refresh(r, org_slug="acme")
    assert cs.get_config("acme")["rag_enabled"] is False, "config disable did NOT propagate"

    # 3. UPDATE — re-enable + change a threshold → propagates
    await r.set(key, json.dumps({"rag_enabled": True, "prompt_injection_threshold": 0.5}))
    await cs._refresh(r, org_slug="acme")
    assert cs.get_config("acme")["rag_enabled"] is True
    assert cs.get_config("acme")["prompt_injection_threshold"] == 0.5

    # 4. DELETE (Redis miss) — keep last-good (deliberate safety tradeoff, not stale-forever
    #    for updates). A transient blip must not wipe an org's firewall config.
    await r.delete(key)
    await cs._refresh(r, org_slug="acme")
    assert cs.get_config("acme")["rag_enabled"] is True  # last-good retained


async def test_per_org_refresh_does_not_bleed_into_other_orgs():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cs = ConfigSync(redis_url="redis://localhost:6379/0", config={"rag_enabled": True})
    await r.set(f"{REDIS_KEY_PREFIX}orgA", json.dumps({"rag_enabled": False}))
    await cs._refresh(r, org_slug="orgA")
    # orgB never configured → falls back to global default, NOT orgA's disabled value
    assert cs.get_config("orgA")["rag_enabled"] is False
    assert cs.get_config("orgB")["rag_enabled"] is True  # global default, no cross-tenant bleed
