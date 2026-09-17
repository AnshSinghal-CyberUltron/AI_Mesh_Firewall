"""Routing isolation honesty: org routing stays active under kill-switch constraint.

KS must not skip LLM_ROUTER.select_model when org routing_enabled is on.
An uncallable isolation fallback must 503 before / without retrying LiteLLM 404.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from ai_mesh_gateway.llm_router import LLMRouter
from ai_mesh_gateway.tests import test_openai_sdk_compat as T
from ai_mesh_gateway.tests.test_m1_6_isolation_killswitch_sdk import (
    ALT,
    MSG,
    PRIMARY,
    UPSTREAM,
    _cfg,
    _env_ctx,
    _expect_503,
    _ks,
)


def _bind_real_router(lr, names):
    real = LLMRouter.__new__(LLMRouter)
    real._config = {"litellm_default_model": ""}
    real._active_model_names = list(names)
    real._qualified_model_names = set(names)
    lr.select_model = real.select_model
    lr.resolve_runtime_selection = real.resolve_runtime_selection
    return lr


@pytest.mark.asyncio
async def test_org_routing_on_killswitch_still_runs_select_model(monkeypatch):
    """Org Dynamic Routing Enabled + KS PRIMARY→ALT: adjudicator runs, target wins."""
    async with _env_ctx(monkeypatch, config=_cfg(routing_enabled=True)) as e:
        import ai_mesh_gateway.main as gm

        _bind_real_router(gm.LLM_ROUTER, [PRIMARY, ALT])
        await e.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=e.app),
            base_url="http://testserver",
        ) as raw:
            resp = await raw.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {T.API_KEY}"},
                json={"model": PRIMARY, "messages": MSG},
            )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert [b["model"] for b in UPSTREAM] == [ALT]
        zs = payload.get("zeroshield") or {}
        routing = zs.get("routing") or {}
        assert routing.get("decision_source") == "kill_switch", routing
        assert routing.get("org_routing_enabled") is True, routing
        assert int(routing.get("candidate_count") or 0) >= 1, routing
        stages = (payload.get("pipeline_trace") or {}).get("stages") or []
        mr = next((s for s in stages if s.get("name") == "model_routing"), None)
        assert mr is not None, stages
        assert mr.get("action") != "skip", mr


@pytest.mark.asyncio
async def test_uncallable_isolation_fallback_fails_closed_before_litellm(monkeypatch):
    """Catalog name with empty model_id: 503 isolation_target_uncallable, no upstream."""
    dead = {
        "model_name": ALT,
        "model_id": "",
        "provider": "openai",
        "is_active": True,
        "api_key_set": True,
    }
    async with _env_ctx(
        monkeypatch,
        config=_cfg(routing_enabled=True),
        models=[dict(T.TEST_MODEL), dead, dict(T.EMBED_MODEL)],
    ) as e:
        import ai_mesh_gateway.main as gm

        _bind_real_router(gm.LLM_ROUTER, [PRIMARY, ALT])
        await e.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
        err = await _expect_503(
            lambda: e.client.chat.completions.create(model=PRIMARY, messages=MSG),
            code=None,
        )
        assert err.status_code == 503
        body = err.body or {}
        nested = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = body.get("code") or nested.get("code")
        assert code in {"isolation_target_uncallable", "kill_switch_active"}
        assert nested.get("type") != "upstream_error"
        assert UPSTREAM == [], f"LiteLLM was invoked for an uncallable fallback: {UPSTREAM}"


@pytest.mark.asyncio
async def test_isolation_provider_404_remaps_to_503_not_upstream_error(monkeypatch):
    """Callable-looking fallback that LiteLLM 404s: remap to isolation 503, no retry."""
    async with _env_ctx(monkeypatch, config=_cfg(routing_enabled=True)) as e:
        import ai_mesh_gateway.main as gm

        _bind_real_router(gm.LLM_ROUTER, [PRIMARY, ALT])
        calls = {"n": 0}

        async def _nf(body, redacted_prompt=None, **kw):
            calls["n"] += 1
            UPSTREAM.append(dict(body))
            return 404, {"error": {"message": "litellm.NotFoundError: model not found", "type": "not_found"}}

        gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=_nf)
        await e.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
        err = await _expect_503(
            lambda: e.client.chat.completions.create(model=PRIMARY, messages=MSG),
            code="isolation_target_uncallable",
        )
        assert err.status_code == 503
        assert (err.body or {}).get("code") == "isolation_target_uncallable"
        assert calls["n"] == 1, "LiteLLM must not be retried after isolation 404"


@pytest.mark.asyncio
async def test_non_isolation_provider_401_stays_sanitized_upstream(monkeypatch):
    """Ordinary Model Connections 401 is not rewritten as isolation_target_uncallable."""
    async with _env_ctx(monkeypatch, config=_cfg(routing_enabled=False)) as e:
        import ai_mesh_gateway.main as gm

        async def _unauth(body, redacted_prompt=None, **kw):
            UPSTREAM.append(dict(body))
            return 401, {"error": {"message": "invalid api key", "type": "auth"}}

        gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=_unauth)
        with pytest.raises(openai.APIStatusError) as exc:
            await e.client.chat.completions.create(model=PRIMARY, messages=MSG)
        assert exc.value.status_code == 401
        blob = str(exc.value.body or {})
        assert "isolation_target_uncallable" not in blob
