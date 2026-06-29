"""M-19: defensive schema validation for Redis-sourced config payloads.

Covers:
- validate_config_payload: dict-shape + per-key type checks (warn+drop)
- validate_model_config_payload: models/routing/fallback_chains shapes
- ConfigSync._load_initial / _refresh / _reload_llm_models fail-open:
  malformed entries are skipped and the last-good config is retained.
"""

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from ai_mesh_gateway import config_sync as cs_mod
from ai_mesh_gateway.config_sync import (
    ConfigSync,
    validate_config_payload,
    validate_model_config_payload,
)


# ── validate_config_payload (pure function) ────────────────────────────────


def test_non_dict_payload_rejected():
    assert validate_config_payload([1, 2, 3], source="t") is None
    assert validate_config_payload("firewall_enabled=true", source="t") is None
    assert validate_config_payload(None, source="t") is None
    assert validate_config_payload(42, source="t") is None


def test_wrong_typed_known_keys_dropped_rest_kept():
    payload = {
        "firewall_enabled": "false",  # truthy string would invert semantics
        "enforcement_mode": "monitor",  # valid
        "requests_per_minute": "1000",  # str where number expected
        "blocked_keywords": "a,b",  # str where list expected
        "toxicity_threshold": 0.4,  # valid float
    }
    out = validate_config_payload(payload, source="t")
    assert out == {"enforcement_mode": "monitor", "toxicity_threshold": 0.4}


def test_bool_not_accepted_for_numeric_key():
    # bool is an int subclass; must not slip through numeric checks
    out = validate_config_payload({"toxicity_threshold": True}, source="t")
    assert out == {}


def test_int_accepted_for_float_key_and_bool_keys_strict():
    out = validate_config_payload(
        {"toxicity_threshold": 1, "firewall_enabled": True, "rag_enabled": 0},
        source="t",
    )
    assert out == {"toxicity_threshold": 1, "firewall_enabled": True}


def test_nullable_tristate_keys_allow_none():
    out = validate_config_payload(
        {"tier2_enabled": None, "mcp_tier2_enabled": None, "firewall_enabled": None},
        source="t",
    )
    assert out == {"tier2_enabled": None, "mcp_tier2_enabled": None}


def test_unknown_keys_pass_through():
    out = validate_config_payload({"future_flag": {"nested": 1}}, source="t")
    assert out == {"future_flag": {"nested": 1}}


def test_non_string_keys_dropped():
    out = validate_config_payload({1: "x", "enforcement_mode": "block"}, source="t")
    assert out == {"enforcement_mode": "block"}


# ── Group A: rag_redaction_enabled / rag_tier2_enabled (strict bool keys) ────


def test_group_a_rag_keys_accept_bool():
    out = validate_config_payload(
        {"rag_redaction_enabled": True, "rag_tier2_enabled": False}, source="t"
    )
    assert out == {"rag_redaction_enabled": True, "rag_tier2_enabled": False}


@pytest.mark.parametrize("key", ["rag_redaction_enabled", "rag_tier2_enabled"])
@pytest.mark.parametrize(
    "bad", ["true", "false", 1, 0, 1.0, None, [True], {"x": 1}]
)
def test_group_a_rag_keys_reject_non_bool(key, bad):
    # These keys are NOT tri-state — unlike tier2_enabled, ``null`` is rejected.
    # A truthy string ("true") must never slip through and silently flip RAG
    # redaction / tier-2 enforcement semantics (the M-19 poisoning threat).
    out = validate_config_payload({key: bad}, source="t")
    assert out == {}


@pytest.mark.asyncio
async def test_group_a_refresh_keeps_last_good_on_mistyped_rag_keys(fake_redis):
    # Last-good: both enabled. A poisoned payload (string/int) must keep the
    # last-good bools while still applying a co-resident valid key.
    config = {
        "rag_redaction_enabled": True,
        "rag_tier2_enabled": True,
        "enforcement_mode": "block",
    }
    sync = ConfigSync("redis://unused", config)
    await fake_redis.set(
        "firewall:config",
        json.dumps(
            {
                "rag_redaction_enabled": "false",  # truthy string poison
                "rag_tier2_enabled": 0,            # int poison
                "enforcement_mode": "monitor",     # valid sibling
            }
        ),
    )
    await sync._refresh(fake_redis)
    assert config["rag_redaction_enabled"] is True   # last-good retained
    assert config["rag_tier2_enabled"] is True       # last-good retained
    assert config["enforcement_mode"] == "monitor"   # valid key applied


# ── validate_model_config_payload (pure function) ───────────────────────────


def test_model_payload_bare_list_accepted_and_filtered():
    out = validate_model_config_payload([{"model_name": "a"}, "junk", 3], source="t")
    assert out == {"models": [{"model_name": "a"}]}


def test_model_payload_scalar_rejected():
    assert validate_model_config_payload("oops", source="t") is None
    assert validate_model_config_payload(7, source="t") is None


def test_model_payload_malformed_sections_dropped():
    out = validate_model_config_payload(
        {
            "models": [{"model_name": "a"}],
            "routing": "not-a-list",
            "fallback_chains": ["not", "a", "dict"],
        },
        source="t",
    )
    assert out == {"models": [{"model_name": "a"}]}


def test_model_payload_valid_sections_normalized():
    out = validate_model_config_payload(
        {
            "models": [{"model_name": "a"}],
            "routing": [{"model_name": "a"}, None],
            "fallback_chains": {"a": ["b"]},
        },
        source="t",
    )
    assert out == {
        "models": [{"model_name": "a"}],
        "routing": [{"model_name": "a"}],
        "fallback_chains": {"a": ["b"]},
    }


# ── ConfigSync integration (fakeredis) ──────────────────────────────────────


def _patch_from_url(monkeypatch, fake_client):
    monkeypatch.setattr(
        cs_mod.aioredis.Redis, "from_url", lambda *a, **k: fake_client
    )


@pytest.mark.asyncio
async def test_load_initial_skips_malformed_org_payload(monkeypatch, fake_redis):
    await fake_redis.set("firewall:config", json.dumps({"enforcement_mode": "block"}))
    await fake_redis.set("firewall:config:badorg", json.dumps([1, 2, 3]))
    await fake_redis.set(
        "firewall:config:goodorg", json.dumps({"enforcement_mode": "monitor"})
    )
    _patch_from_url(monkeypatch, fake_redis)

    config = {"enforcement_mode": "env-default"}
    sync = ConfigSync("redis://unused", config)
    await sync._load_initial()

    assert "badorg" not in sync._config_by_org
    assert sync.get_config("goodorg")["enforcement_mode"] == "monitor"
    assert sync.is_loaded


@pytest.mark.asyncio
async def test_load_initial_invalid_json_global_key_continues(monkeypatch, fake_redis):
    await fake_redis.set("firewall:config", "{not json")
    await fake_redis.set(
        "firewall:config:org1", json.dumps({"enforcement_mode": "monitor"})
    )
    _patch_from_url(monkeypatch, fake_redis)

    config = {"enforcement_mode": "env-default"}
    sync = ConfigSync("redis://unused", config)
    await sync._load_initial()

    # Bad global key skipped, org key still loaded (no abort mid-scan)
    assert sync.get_config("org1")["enforcement_mode"] == "monitor"


@pytest.mark.asyncio
async def test_refresh_malformed_payload_keeps_last_good(fake_redis):
    config = {"enforcement_mode": "env-default", "firewall_enabled": True}
    sync = ConfigSync("redis://unused", config)

    await fake_redis.set(
        "firewall:config", json.dumps({"enforcement_mode": "monitor"})
    )
    await sync._refresh(fake_redis)
    assert config["enforcement_mode"] == "monitor"

    # Non-dict payload: warn + keep last-good
    await fake_redis.set("firewall:config", json.dumps(["broken"]))
    await sync._refresh(fake_redis)
    assert config["enforcement_mode"] == "monitor"

    # Invalid JSON: warn + keep last-good
    await fake_redis.set("firewall:config", "{not json")
    await sync._refresh(fake_redis)
    assert config["enforcement_mode"] == "monitor"


@pytest.mark.asyncio
async def test_refresh_drops_only_mistyped_keys(fake_redis):
    config = {"enforcement_mode": "block", "firewall_enabled": True}
    sync = ConfigSync("redis://unused", config)

    await fake_redis.set(
        "firewall:config",
        json.dumps({"firewall_enabled": "false", "enforcement_mode": "monitor"}),
    )
    await sync._refresh(fake_redis)

    assert config["enforcement_mode"] == "monitor"  # valid key applied
    assert config["firewall_enabled"] is True  # mistyped key kept last-good


@pytest.mark.asyncio
async def test_org_refresh_does_not_bleed_into_global_or_other_orgs(fake_redis):
    """Per-org pub/sub refresh must not overwrite global CONFIG or other tenants."""
    config = {"enforcement_mode": "block", "firewall_enabled": True}
    sync = ConfigSync("redis://unused", config)

    await fake_redis.set("firewall:config", json.dumps({"enforcement_mode": "block"}))
    await fake_redis.set(
        "firewall:config:org-a", json.dumps({"enforcement_mode": "monitor"})
    )
    await fake_redis.set(
        "firewall:config:org-b", json.dumps({"enforcement_mode": "flag"})
    )

    await sync._refresh(fake_redis)
    await sync._refresh(fake_redis, org_slug="org-a")
    await sync._refresh(fake_redis, org_slug="org-b")
    assert config["enforcement_mode"] == "block"
    assert sync.get_config("org-a")["enforcement_mode"] == "monitor"
    assert sync.get_config("org-b")["enforcement_mode"] == "flag"

    # Hot-reload org-b only — org-a and global must stay unchanged.
    await fake_redis.set(
        "firewall:config:org-b", json.dumps({"enforcement_mode": "block"})
    )
    await sync._refresh(fake_redis, org_slug="org-b")

    assert config["enforcement_mode"] == "block"
    assert sync.get_config("org-a")["enforcement_mode"] == "monitor"
    assert sync.get_config("org-b")["enforcement_mode"] == "block"


@pytest.mark.asyncio
async def test_reload_llm_models_skips_malformed_keys(monkeypatch, fake_redis):
    stub_main = types.ModuleType("ai_mesh_gateway.main")
    stub_main.LLM_ROUTER = MagicMock()
    monkeypatch.setitem(sys.modules, "ai_mesh_gateway.main", stub_main)
    import ai_mesh_gateway

    monkeypatch.setattr(ai_mesh_gateway, "main", stub_main, raising=False)

    await fake_redis.set(
        "llm:model_configs:org1",
        json.dumps(
            {
                "models": [{"model_name": "good-model"}],
                "routing": [{"model_name": "good-model"}],
                "fallback_chains": {"good-model": []},
            }
        ),
    )
    await fake_redis.set("llm:model_configs:org2", "{not json")
    await fake_redis.set(
        "llm:model_configs:org3",
        json.dumps({"models": "oops", "routing": "oops"}),
    )

    sync = ConfigSync("redis://unused", {})
    sync._model_routing_by_org["org3"] = [{"model_name": "last-good"}]

    await sync._reload_llm_models(fake_redis)

    stub_main.LLM_ROUTER.reload_models.assert_called_once_with(
        [{"model_name": "good-model", "_zs_org": "org1"}]
    )
    assert sync.get_model_routing("org1") == [{"model_name": "good-model"}]
    assert sync.get_fallback_chains("org1") == {"good-model": []}
    # malformed routing for org3 keeps last-good routing
    assert sync.get_model_routing("org3") == [{"model_name": "last-good"}]
    # org2 (invalid JSON) never created
    assert "org2" not in sync._model_routing_by_org
