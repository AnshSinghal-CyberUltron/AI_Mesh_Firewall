"""Claim §10.1.5 row 1: tenants inherit platform posture via ConfigSync.

Drives the REAL ConfigSync._load_initial + get_config/get_own_config against
fakeredis (async), with payloads shaped like the control plane's
FirewallConfig.build_gateway_payload() (which ALWAYS emits enforcement_mode and
tier2_enabled, tier2_enabled=None by default).
"""
import asyncio
import json
import sys

import fakeredis
import redis.asyncio as aioredis

from ai_mesh_gateway import config_sync as cs
from ai_mesh_gateway.config import load_config

server = fakeredis.FakeServer()


def _fake_from_url(url, **kw):
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=kw.get("decode_responses", False))


aioredis.Redis.from_url = staticmethod(_fake_from_url)  # type: ignore[assignment]


async def main():
    seed = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    # Platform ("default" = FirewallConfig with organization NULL) posture.
    await seed.set("firewall:config:default", json.dumps({
        "firewall_enabled": True, "enforcement_mode": "block", "tier2_enabled": True,
        "rag_tier2_enabled": True, "log_level": "detailed", "org_slug": "default",
    }))
    # Tenant A has a row; control plane ALWAYS emits enforcement_mode + tier2_enabled
    # (tier2_enabled default None = "no per-org opinion").
    await seed.set("firewall:config:acme", json.dumps({
        "firewall_enabled": True, "enforcement_mode": "monitor", "tier2_enabled": None,
        "log_level": "detailed", "org_slug": "acme",
    }))
    # Tenant P: a PARTIAL payload that omits the two keys (not what the current
    # control plane writes, but what {**self._config, **data} does with it).
    await seed.set("firewall:config:partial", json.dumps({"firewall_enabled": True}))

    config = load_config()
    print("global CONFIG has enforcement_mode:", "enforcement_mode" in config,
          "| tier2_enabled:", "tier2_enabled" in config)
    sync = cs.ConfigSync(redis_url="redis://fake", config=config)
    await sync._load_initial()
    print("orgs loaded:", sorted(sync._config_by_org))

    for slug in ("acme", "partial", "newco-no-row", ""):
        c = sync.get_config(slug)
        own = sync.get_own_config(slug)
        eff = cs.resolve_tier2_enabled(c.get("tier2_enabled"))
        print(f"get_config({slug!r:16}) -> enforcement_mode={c.get('enforcement_mode')!r:9} "
              f"tier2_enabled={c.get('tier2_enabled')!r:5} (effective Tier-2 {'ON' if eff else 'OFF'}) "
              f"rag_tier2_enabled={c.get('rag_tier2_enabled')!r:5} | own_config={'None' if own is None else 'present'} "
              f"| same-object-as-default={c is sync._config_by_org.get('default')}")

    # Now show the merge path with the retired global key applied (self._config mutated).
    sync._apply({"enforcement_mode": "block", "tier2_enabled": True})
    await sync._refresh(seed, org_slug="partial")
    c = sync.get_config("partial")
    print("after global _apply + refresh(partial):", {k: c.get(k) for k in ("enforcement_mode", "tier2_enabled")})


asyncio.run(main())
