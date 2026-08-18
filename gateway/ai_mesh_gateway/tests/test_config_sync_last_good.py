"""Task 5: last-good model catalog, copy-on-read, no per-request Redis client.

Root cause of 422 no_provider_configured at high in-flight:
- get_model_routing returns the live list (callers can mutate / .clear()).
- reload_models_now opens a fresh Redis.from_url every chat, then aclose.
- empty routing=[] clobbers last-good.
- per-org reload_models replaces the whole LiteLLM router with one tenant.
"""

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from ai_mesh_gateway import config_sync as cs_mod
from ai_mesh_gateway.config_sync import ConfigSync


def _stub_llm_router(monkeypatch) -> MagicMock:
    stub_main = types.ModuleType("ai_mesh_gateway.main")
    stub_main.LLM_ROUTER = MagicMock()
    stub_main.REDIS_CLIENT = None
    monkeypatch.setitem(sys.modules, "ai_mesh_gateway.main", stub_main)
    import ai_mesh_gateway

    monkeypatch.setattr(ai_mesh_gateway, "main", stub_main, raising=False)
    return stub_main.LLM_ROUTER


def test_get_model_routing_returns_copy():
    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["acme"] = [{"model_name": "m"}]
    got = sync.get_model_routing("acme")
    got.clear()
    assert sync.get_model_routing("acme") == [{"model_name": "m"}]
    got2 = sync.get_model_routing("acme")
    got2[0]["model_name"] = "mutated"
    assert sync.get_model_routing("acme") == [{"model_name": "m"}]


@pytest.mark.asyncio
async def test_reload_timeout_keeps_last_good(monkeypatch):
    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["acme"] = [{"model_name": "kept"}]

    def boom(*_a, **_k):
        raise TimeoutError("redis")

    monkeypatch.setattr(cs_mod.aioredis.Redis, "from_url", boom)
    await sync.reload_models_now(org_slug="acme")
    assert sync.get_model_routing("acme") == [{"model_name": "kept"}]


@pytest.mark.asyncio
async def test_empty_routing_payload_does_not_clobber(fake_redis, monkeypatch):
    _stub_llm_router(monkeypatch)
    await fake_redis.set(
        "llm:model_configs:acme",
        json.dumps(
            {
                "models": [{"model_name": "acme-gpt"}],
                "routing": [],
            }
        ),
    )
    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["acme"] = [{"model_name": "kept"}]
    await sync._reload_llm_models(fake_redis, org_slug="acme")
    assert sync.get_model_routing("acme") == [{"model_name": "kept"}]


@pytest.mark.asyncio
async def test_org_reload_merges_not_replaces_router(fake_redis, monkeypatch):
    router = _stub_llm_router(monkeypatch)
    inner = MagicMock()
    inner.model_list = [
        {
            "model_name": "a::a-gpt",
            "_zs_org": "a",
            "litellm_params": {"model": "openai/a"},
        },
    ]
    router._router = inner

    await fake_redis.set(
        "llm:model_configs:b",
        json.dumps(
            {
                "models": [
                    {
                        "model_name": "b-gpt",
                        "litellm_params": {"model": "openai/b"},
                    }
                ],
                "routing": [{"model_name": "b-gpt"}],
            }
        ),
    )
    sync = ConfigSync("redis://unused", {})
    await sync._reload_llm_models(fake_redis, org_slug="b")

    router.reload_models.assert_called_once()
    loaded = router.reload_models.call_args[0][0]
    orgs = {entry.get("_zs_org") for entry in loaded if isinstance(entry, dict)}
    assert "a" in orgs, "org A deployments must survive a B-only reload"
    assert "b" in orgs, "org B deployments must be present after B reload"
    b_only = all(entry.get("_zs_org") == "b" for entry in loaded if isinstance(entry, dict))
    assert not b_only, "reload_models must not be called with ONLY B"
