"""GW11: drive the REAL ConfigSync.reload_models_now exactly as proxy_chat does
(main.py:7704-7706: `if not routing_models: await CONFIG_SYNC.reload_models_now(org_slug=...)`).

- fake Redis (fakeredis) with 5 ms injected latency per GET
- fake shared LLM_ROUTER installed on the real ai_mesh_gateway.main module
Measures: (1) per-org lock serialization of concurrent requests from an org with
no cached routing; (2) request-path rebuild of the SHARED router (reload_models
called with every org's deployments merged).
"""
import asyncio
import json
import time

import fakeredis

import ai_mesh_gateway.main as gm
from ai_mesh_gateway import config_sync as cs

LAT = 0.005
server = fakeredis.FakeServer()


class SlowRedis(fakeredis.FakeAsyncRedis):
    async def get(self, key):
        await asyncio.sleep(LAT)
        return await super().get(key)


class FakeInner:
    def __init__(self, model_list):
        self.model_list = model_list


class FakeRouter:
    def __init__(self, model_list):
        self._router = FakeInner(model_list)
        self.reloads = []

    def reload_models(self, model_list):  # synchronous, like LLMRouter.reload_models
        self.reloads.append([(m.get("_zs_org"), m.get("model_name")) for m in model_list])
        self._router = FakeInner([dict(m) for m in model_list])


def payload(slug, name):
    return json.dumps({
        "models": [{"model_name": name, "litellm_params": {"model": f"openai/{name}"}}],
        "routing": [{"model_name": name, "model_id": name, "is_active": True}],
    })


async def main():
    seed = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    await seed.set("llm:model_configs:acme", payload("acme", "gpt-4o-mini"))
    await seed.set("llm:model_configs:beta", payload("beta", "claude-haiku"))
    # org "zeta" has NO llm:model_configs key -> routing stays empty -> every request reloads

    sync = cs.ConfigSync(redis_url="redis://fake", config={})
    sync._redis = SlowRedis(server=server, decode_responses=True)
    gm.LLM_ROUTER = FakeRouter([
        {"model_name": "acme::gpt-4o-mini", "_zs_org": "acme"},
        {"model_name": "beta::claude-haiku", "_zs_org": "beta"},
    ])

    # (1) 50 concurrent chat requests from org "zeta" (no cached routing)
    N = 50
    t0 = time.perf_counter()
    await asyncio.gather(*[sync.reload_models_now(org_slug="zeta") for _ in range(N)])
    dt = (time.perf_counter() - t0) * 1000
    print(f"(1) {N} concurrent reload_models_now('zeta') with {LAT*1000:.0f} ms Redis GET: "
          f"{dt:.0f} ms total (serialized ~= {N*LAT*1000:.0f} ms; parallel would be ~{LAT*1000:.0f} ms); "
          f"routing still empty: {sync.get_model_routing('zeta') == []}")

    # (2) org acme + beta each hit the cold-cache path concurrently (different locks)
    sync._model_routing_by_org.clear()
    await asyncio.gather(sync.reload_models_now(org_slug="acme"), sync.reload_models_now(org_slug="beta"))
    print("(2) shared LLM_ROUTER.reload_models calls made from request path:", len(gm.LLM_ROUTER.reloads))
    for i, r in enumerate(gm.LLM_ROUTER.reloads):
        print(f"    reload #{i+1}: {r}")
    print("    final shared router model_list:", [(m.get('_zs_org'), m.get('model_name')) for m in gm.LLM_ROUTER._router.model_list])
    print("    per-org locks created:", sorted(sync._reload_locks))


asyncio.run(main())
