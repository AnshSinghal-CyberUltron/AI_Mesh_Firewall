"""MODULE 1.6 — Inline Model Isolation & Kill-Switch, via the stock OpenAI SDK.
Spec (docs/MODULE1_AI_MESH_FIREWALL.md §1.6): logical isolation per model, separate
credentials, independent rate limits, independent risk scoring; kill-switch immediate
disable and auto-reroute to fallback. Every mutating control is asserted on what the
UPSTREAM STUB ACTUALLY RECEIVED (``UPSTREAM``), not only on the client-visible response.
Run: .venv/bin/python -m pytest <this file> -q -p no:cacheprovider  (from gateway/)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import fakeredis, fakeredis.aioredis, httpx, openai, pytest, pytest_asyncio, uvicorn
import redis.exceptions as redis_exc

import ai_mesh_gateway.main as gm
from ai_mesh_gateway import middleware as gw_middleware
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

PRIMARY, ALT, EMBED = "gpt-4o-mini", "gpt-4o-alt", "zs-embed"
ALT_MODEL = {"model_name": ALT, "model_id": ALT, "provider": "openai",
             "is_active": True, "api_key_set": True}
KEYHASH = hashlib.sha256(T.API_KEY.encode()).hexdigest()
MSG = [{"role": "user", "content": "hi"}]
async def _aval(v): return v          # AuthMiddleware._get_redis is awaited
UPSTREAM: list[dict] = []       # every body handed to the upstream router
STREAM_KWARGS: list[dict] = []  # kwargs handed to acompletion_stream
pytestmark = pytest.mark.asyncio

def _cfg(**over) -> dict:
    return {**T.TEST_CONFIG, "kill_switch_enabled": True, "firewall_enabled": True, **over}

def _auth(allowed=(PRIMARY, ALT, EMBED)) -> dict:
    return dict(T._auth_payload(), allowed_models=list(allowed))

def _ks(action="disable", fallback="") -> str:      # kill_switch:* payload
    return json.dumps({"is_active": True, "action": action,
                       "fallback_model": fallback, "reason": "operator halt"})

def _ms() -> str:                                    # model_state:* payload (risk isolate)
    return json.dumps({"status": "isolated", "action": "block", "risk_score": 99.0,
                       "threshold": 80.0, "isolation_reason": "risk threshold breached"})

async def _cap_completion(body, redacted_prompt=None, **kw):
    UPSTREAM.append(dict(body)); return await T._fake_completion(body, redacted_prompt, **kw)

async def _cap_embedding(body, *a, **kw):
    UPSTREAM.append(dict(body)); return await T._fake_embedding(body, *a, **kw)

async def _cap_stream(body, redacted_prompt=None, metrics=None, **kw):
    UPSTREAM.append(dict(body)); STREAM_KWARGS.append(dict(kw))
    async for f in T._fake_stream(body, redacted_prompt, metrics=metrics, **kw):
        yield f

def _singletons(config, redis_client, models, rate_limiter, agent_id) -> dict:
    """The gateway globals every harness here installs (ASGI via monkeypatch, live via
    setattr). extract_usage / estimate_prompt_tokens must be the REAL helpers — a
    MagicMock there leaks an unserializable object into the response body."""
    from ai_mesh_gateway.llm_router import LLMRouter as _LR
    cs, lr = MagicMock(), MagicMock()
    cs.get_config = MagicMock(return_value=dict(config))
    cs.get_model_routing = MagicMock(return_value=list(
        models if models is not None
        else [dict(T.TEST_MODEL), dict(ALT_MODEL), dict(T.EMBED_MODEL)]))
    cs.reload_models_now, cs.get_fallback_chains = AsyncMock(), MagicMock(return_value={})
    lr.acompletion = AsyncMock(side_effect=_cap_completion)
    lr.aembedding = AsyncMock(side_effect=_cap_embedding)
    lr.acompletion_stream = _cap_stream
    lr.get_model_list = MagicMock(return_value=[{"id": PRIMARY, "object": "model"}])
    lr.extract_usage, lr.estimate_prompt_tokens = _LR.extract_usage, _LR.estimate_prompt_tokens
    return {"CONFIG": dict(config), "CONFIG_SYNC": cs, "LLM_ROUTER": lr, "AGENT_ID": agent_id,
            "INPUT_SCANNER": InputScanner(thread_pool_size=2), "POLICY_SYNC": None,
            "RATE_LIMITER": rate_limiter, "CIRCUIT_BREAKER": None, "TELEMETRY": None,
            "REDIS_CLIENT": redis_client, "OUTPUT_GUARD": None,
            "_emit_telemetry": lambda **k: None, "_audit_fire_and_forget": lambda **k: None}

class _Env:
    def __init__(self, app, state, client):
        self.app, self.state, self.client = app, state, client
@contextlib.asynccontextmanager
async def _env_ctx(monkeypatch, *, config=None, allowed=(PRIMARY, ALT, EMBED),
                   redis="fake", models=None, rate_limiter=None, agent_id=None):
    """Real gateway app on ASGITransport: auth on fakeredis, upstream captured."""
    UPSTREAM.clear(); STREAM_KWARGS.clear()
    state = fakeredis.aioredis.FakeRedis(decode_responses=True) if redis == "fake" else redis
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await auth_redis.set(f"auth:apikey:{KEYHASH}", json.dumps(_auth(allowed)))
    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis",
                        lambda _self: _aval(auth_redis))
    for name, val in _singletons(config or _cfg(), state, models, rate_limiter, agent_id).items():
        monkeypatch.setattr(gm, name, val)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=gm.app), base_url="http://testserver")
    c = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=T.API_KEY,
                           http_client=http, max_retries=0)
    try:
        yield _Env(gm.app, state, c)
    finally:
        await c.close()
        if hasattr(state, "aclose"):
            await state.aclose()
        await auth_redis.aclose()

@pytest_asyncio.fixture()
async def env(monkeypatch):
    """kill-switch ON, live fakeredis, 3-model catalog, standalone (no control plane)."""
    async with _env_ctx(monkeypatch) as e:
        yield e

class _BrokenRedis:
    """Every kill-switch read raises a redis ConnectionError."""
    def pipeline(self, *a, **kw):
        raise redis_exc.ConnectionError("redis down")
    async def get(self, *a, **kw):
        raise redis_exc.ConnectionError("redis down")

async def _expect_503(call, code="kill_switch_active"):
    with pytest.raises(openai.APIStatusError) as exc:
        await call()
    assert exc.value.status_code == 503, f"expected fail-closed 503, got {exc.value.status_code}"
    assert not code or (exc.value.body or {}).get("code") == code
    return exc.value

async def test_killswitch_disable_refuses_only_the_killed_model(env):
    """Operator disable => 503 kill_switch_active with upstream never invoked, while the
    logically-isolated peer model ALT keeps serving (no cross-model bleed)."""
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
    err = await _expect_503(lambda: env.client.chat.completions.create(model=PRIMARY, messages=MSG))
    assert isinstance(err, openai.InternalServerError)
    assert UPSTREAM == [], "kill-switched model still reached the upstream provider"
    r = await env.client.chat.completions.create(model=ALT, messages=MSG)
    assert r.choices[0].message.content == "Hello from upstream."
    assert [b["model"] for b in UPSTREAM] == [ALT]
    # …until the org_global scope trips: a blanket halt kills ALT too, and is forced
    # to action='disable' even though this payload asks to reroute.
    UPSTREAM.clear()
    await env.state.set("kill_switch:default:global", _ks("reroute", ALT))
    await _expect_503(lambda: env.client.chat.completions.create(model=ALT, messages=MSG))
    assert UPSTREAM == []

async def test_credential_scope_is_narrow_and_takes_precedence(env):
    """A credential switch for a DIFFERENT key prefix must not disable this caller; the
    one for THIS prefix must, and a credential DISABLE beats a model-level REROUTE
    (precedence credential > org_model)."""
    await env.state.set(f"kill_switch:default:credential:zs_other:model:{PRIMARY}", _ks())
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
    r = await env.client.chat.completions.create(model=PRIMARY, messages=MSG)
    assert [b["model"] for b in UPSTREAM] == [ALT]   # other-prefix switch ignored
    assert r.choices[0].message.content == "Hello from upstream."
    UPSTREAM.clear()
    await env.state.set(f"kill_switch:default:credential:{T.API_KEY[:8]}:model:{PRIMARY}", _ks())
    await _expect_503(lambda: env.client.chat.completions.create(model=PRIMARY, messages=MSG))
    assert UPSTREAM == [], "credential-scoped disable was overridden by a model reroute"

@pytest.mark.parametrize("variant", ["GPT-4O-MINI", " gpt-4o-mini", "gpt-4o-mini "])
async def test_killswitch_not_evaded_by_model_casing_or_padding(env, variant):
    """BYPASS PROBE: the switch key is built from the raw client model string."""
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
    with contextlib.suppress(openai.APIStatusError):
        await env.client.chat.completions.create(model=variant, messages=MSG)
    assert UPSTREAM == [], f"kill-switch EVADED with model={variant!r}: {UPSTREAM}"

async def test_reroute_dispatches_fallback_with_clean_credential_identity(env):
    """action=reroute is REAL dispatch, not a label swap: the upstream body carries the
    FALLBACK model plus the tenant slug (so the router selects THAT model's BYOK key),
    and the killed original is never smuggled alongside it."""
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
    r = await env.client.chat.completions.create(model=PRIMARY, messages=MSG)
    assert r.choices[0].message.content == "Hello from upstream."
    assert [b["model"] for b in UPSTREAM] == [ALT]
    body = UPSTREAM[0]
    assert "_zs_org_slug" in body, "org tag missing — router cannot org-qualify the BYOK key"
    leaked = [k for k, v in body.items() if isinstance(v, str) and v == PRIMARY]
    assert leaked == [], f"killed model identity leaked into the dispatch body: {leaked}"

async def _reroute_response(env) -> dict:
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app),
                                 base_url="http://testserver") as raw:
        resp = await raw.post("/v1/chat/completions",
                              headers={"Authorization": f"Bearer {T.API_KEY}"},
                              json={"model": PRIMARY, "messages": MSG})
    assert resp.status_code == 200
    return resp.json()

# GAP CLOSED (I-02): the unregistered path now returns through the connected branch,
# which attaches the kill-switch reroute route_metadata to `zeroshield`. xfail removed.
async def test_killswitch_reroute_serving_model_is_reported_honestly(env):
    """SPEC: §1.6 auto-reroute must be observable from the standard SDK surface."""
    payload = await _reroute_response(env)
    assert UPSTREAM[0]["model"] == ALT
    zs = payload.get("zeroshield") or {}
    assert payload.get("model") == ALT or ALT in json.dumps(zs), (
        f"client cannot tell it was rerouted — model={payload.get('model')!r}, zeroshield={zs!r}")

async def test_killswitch_reroute_disclosure_is_honest(env):
    """Pins the disclosure surface. `model` still echoes the killed model and a reroute is
    not a security ACTION, but since I-02 the unregistered path returns through the
    connected branch, so the reroute is now disclosed HONESTLY and under correctly-named
    keys: `zeroshield.routing` names the fallback, and the trace files the requested model
    under `requested_model` with the served one under `routed_model` (it previously filed
    the SELECTED model under `requested_model` — the misfiling this test used to pin)."""
    payload = await _reroute_response(env)
    assert UPSTREAM[0]["model"] == ALT
    assert payload["model"] == PRIMARY
    zs = payload.get("zeroshield") or {}
    assert zs.get("action") == "allow"          # a reroute is not a security action
    _routing = zs.get("routing") or {}
    assert _routing.get("routed_model") == ALT, _routing
    assert _routing.get("decision_source") == "kill_switch", _routing
    _trace = payload.get("pipeline_trace") or {}
    assert _trace.get("requested_model") == PRIMARY, _trace.get("requested_model")
    assert _trace.get("routed_model") == ALT, _trace.get("routed_model")

@pytest.mark.parametrize("key,val", [
    (f"kill_switch:default:model:{ALT}", _ks()),     # B7: fallback itself kill-switched
    (f"model_state:default:{ALT}", _ms()),           # G6: fallback model-state ISOLATED
])
async def test_reroute_onto_a_disabled_target_fails_closed(env, key, val):
    """The reroute resolver reads the STATIC catalog, so the chosen fallback must be
    re-checked against BOTH live namespaces and refused if it too is disabled."""
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
    await env.state.set(key, val)
    await _expect_503(lambda: env.client.chat.completions.create(model=PRIMARY, messages=MSG), None)
    assert UPSTREAM == [], "served a fallback that was itself operator-disabled"

@pytest.mark.parametrize("fallback,kw", [
    (ALT, {"allowed": (PRIMARY,)}),                                    # caller not entitled
    (EMBED, {"models": [dict(T.TEST_MODEL), dict(T.EMBED_MODEL)]}),    # R#7 embedding-only
], ids=["outside_caller_allowlist", "embedding_only_model"])
async def test_reroute_onto_an_ineligible_fallback_is_refused(monkeypatch, fallback, kw):
    """Isolation must survive the reroute: an operator fallback the CALLER is not
    entitled to, or one that cannot serve chat at all, must never be granted."""
    async with _env_ctx(monkeypatch, **kw) as e:
        await e.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", fallback))
        await _expect_503(lambda: e.client.chat.completions.create(model=PRIMARY, messages=MSG))
        assert UPSTREAM == [], f"rerouted onto an ineligible model: {UPSTREAM}"

@pytest.mark.parametrize("store", ["broken", None], ids=["redis_error", "redis_absent"])
@pytest.mark.parametrize("surface", ["chat", "embeddings"])
async def test_killswitch_store_failure_fails_closed(monkeypatch, store, surface):
    """FAIL-SAFE DIRECTION: an erroring OR unwired kill-switch store must REFUSE on both
    the chat and the embeddings surface. Fail-open on a kill-switch would be CRITICAL."""
    async with _env_ctx(monkeypatch, redis=_BrokenRedis() if store == "broken" else None) as e:
        call = ((lambda: e.client.chat.completions.create(model=PRIMARY, messages=MSG))
                if surface == "chat"
                else (lambda: e.client.embeddings.create(model=EMBED, input="hello")))
        await _expect_503(call)
        assert UPSTREAM == []

async def test_org_config_flag_can_disable_an_active_killswitch(monkeypatch):
    """Blast radius: kill_switch_enabled=False makes an ACTIVE kill-switch inert."""
    async with _env_ctx(monkeypatch, config=_cfg(kill_switch_enabled=False)) as e:
        await e.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
        r = await e.client.chat.completions.create(model=PRIMARY, messages=MSG)
        assert r.choices[0].message.content == "Hello from upstream."
        assert UPSTREAM[0]["model"] == PRIMARY

async def test_model_isolation_global_allowlist_blocks_unlisted_model(monkeypatch):
    """model_isolation_enabled + allowed_models: unlisted => 403 pre-dispatch."""
    async with _env_ctx(monkeypatch, config=_cfg(model_isolation_enabled=True,
                                                 allowed_models=[PRIMARY])) as e:
        with pytest.raises(openai.PermissionDeniedError) as exc:
            await e.client.chat.completions.create(model=ALT, messages=MSG)
        assert exc.value.status_code == 403
        assert UPSTREAM == []
        r = await e.client.chat.completions.create(model=PRIMARY, messages=MSG)
        assert r.choices[0].message.content == "Hello from upstream."

async def test_risk_isolation_is_per_model_not_shared(env):
    """Independent risk scoring: isolating PRIMARY on risk must not touch ALT."""
    await env.state.set(f"model_state:default:{PRIMARY}", _ms())
    await _expect_503(lambda: env.client.chat.completions.create(model=PRIMARY, messages=MSG), None)
    assert UPSTREAM == []
    r = await env.client.chat.completions.create(model=ALT, messages=MSG)
    assert r.choices[0].message.content == "Hello from upstream."
    assert [b["model"] for b in UPSTREAM] == [ALT]

class _LuaShim:
    """fakeredis has no Lua engine (``lupa`` absent) so EVAL raises and the limiter fails
    open. This runs the counter scripts natively, so the REAL key derivation, org/model
    scoping and ``current > max_rpm`` comparison execute; only atomicity is emulated."""
    def __init__(self, inner):
        self._inner = inner

    async def eval(self, script, numkeys, *args):
        key = args[0]
        if len(args) == 2:                       # per-model RPM: INCR + EXPIRE
            current = await self._inner.incr(key)
            if current == 1 or await self._inner.ttl(key) < 0:
                await self._inner.expire(key, int(args[1]))
            return current
        limit, estimated, ttl = int(args[1]), int(args[2]), int(args[3])
        current = int(await self._inner.get(key) or 0)
        if current + estimated > limit:              # TPM: check-then-INCRBY
            return [0, current]
        new_val = await self._inner.incrby(key, estimated)
        if current == 0 or await self._inner.ttl(key) < 0:
            await self._inner.expire(key, ttl)
        return [1, int(new_val)]

    def __getattr__(self, name):
        return getattr(self._inner, name)

def _rl_env(monkeypatch, state, factory, models, *, connected=True):
    """Env wired to a real RateLimiter. AGENT_ID + backend_url are REQUIRED to reach the
    per-model stage at all (see test_standalone_mode_skips_per_model_rate_limit); the
    unrelated control-plane policy HTTP fallback is short-circuited."""
    from ai_mesh_gateway.rate_limiter import RateLimiter
    lim = RateLimiter("redis://unused")
    monkeypatch.setattr(lim, "_client", lambda: factory(state))
    monkeypatch.setattr(gm, "_policy_check_cached", lambda *a, **kw: (200, {"action": "allow"}))
    return _env_ctx(monkeypatch, redis=state, rate_limiter=lim, models=models,
                    agent_id="agent-test" if connected else None,
                    config=_cfg(backend_url="http://backend") if connected else _cfg())

async def _burn(client, model, n=4):
    codes = []
    for _ in range(n):
        try:
            await client.chat.completions.create(model=model, messages=MSG); codes.append(200)
        except openai.APIStatusError as e:
            codes.append(e.status_code)
    return codes

async def test_per_model_rate_limit_counters_are_independent(monkeypatch):
    """Independent rate limits: exhausting PRIMARY's RPM leaves ALT serving, and the
    counter keys are model-scoped."""
    state = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with _rl_env(monkeypatch, state, _LuaShim,
                       [dict(T.TEST_MODEL, rate_limit_rpm=2),
                        dict(ALT_MODEL, rate_limit_rpm=50)]) as e:
        codes = await _burn(e.client, PRIMARY)
        assert 429 in codes, f"per-model RPM never fired: {codes}"
        r = await e.client.chat.completions.create(model=ALT, messages=MSG)
        assert r.choices[0].message.content == "Hello from upstream."
        keys = sorted(await state.keys("ratelimit:model:*"))
        assert any(PRIMARY in k for k in keys) and any(ALT in k for k in keys), keys

async def test_per_model_rate_limit_fails_open_when_backend_errors(monkeypatch):
    """ASYMMETRY (deliberate per rate_limiter.py L6): the kill-switch fails CLOSED on a
    store error but the per-model RATE LIMIT fails OPEN — traffic keeps flowing."""
    class _NoEval:
        async def eval(self, *a, **kw):
            raise redis_exc.ResponseError("unknown command 'eval'")

    state = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with _rl_env(monkeypatch, state, lambda _s: _NoEval(),
                       [dict(T.TEST_MODEL, rate_limit_rpm=1)]) as e:
        assert await _burn(e.client, PRIMARY) == [200, 200, 200, 200]
        assert len(UPSTREAM) == 4

async def test_unregistered_gateway_still_enforces_per_model_rate_limit(monkeypatch):
    """§1.6 per-model RPM must not depend on control-plane registration. This test
    previously asserted the DEFECT (its docstring named main.py:7563 as a FINDING);
    since I-02 the short-circuit gates on routing-catalogue availability, so an
    unregistered worker with a warm catalogue runs the rate-limit stage and RPM=1 is
    enforced with the counter key written."""
    state = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with _rl_env(monkeypatch, state, _LuaShim,
                       [dict(T.TEST_MODEL, rate_limit_rpm=1)], connected=False) as e:
        codes = await _burn(e.client, PRIMARY)
        assert 429 in codes, f"per-model RPM never fired on the unregistered path: {codes}"
        assert any(PRIMARY in k for k in await state.keys("ratelimit:model:*")), \
            "per-model limiter ran but wrote no counter key"

@pytest.mark.parametrize("payload", [_ks(), _ks("reroute", PRIMARY)], ids=["disable", "reroute"])
async def test_killswitch_enforced_on_embeddings_path(env, payload):
    """Embeddings have no chat fallback, so ANY is_killed verdict must 503 — including
    action=reroute with an otherwise-valid chat fallback."""
    await env.state.set(f"kill_switch:default:model:{EMBED}", payload)
    await _expect_503(lambda: env.client.embeddings.create(model=EMBED, input="hello"))
    assert UPSTREAM == []

async def test_killswitch_survives_the_responses_adapter(env):
    """/v1/responses dispatches internally through proxy_chat: BOTH a disable and a
    reroute must survive the translation rather than be lost in the adapter."""
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
    await _expect_503(lambda: env.client.responses.create(model=PRIMARY, input="hi"))
    assert UPSTREAM == []
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks("reroute", ALT))
    await env.client.responses.create(model=PRIMARY, input="hi")
    assert [b["model"] for b in UPSTREAM] == [ALT]

async def _drain_stream_and_get_check(env):
    stream = await env.client.chat.completions.create(
        model=PRIMARY, messages=[{"role": "user", "content": "stream"}], stream=True)
    _ = [c async for c in stream]
    assert STREAM_KWARGS, "acompletion_stream received no kwargs"
    return STREAM_KWARGS[-1].get("state_check")

async def test_midstream_state_check_is_live_and_fails_open_on_store_error(env, monkeypatch):
    """E13 wiring: the router gets an async state_check for the ACTIVE model that flips
    True the moment an operator trips the switch (a live read, not a start-of-stream
    snapshot). ASYMMETRY (deliberate, main.py:3540): the REQUEST gate fails CLOSED on a
    store error but this MID-STREAM re-check fails OPEN — pinned so it stays visible."""
    check = await _drain_stream_and_get_check(env)
    assert callable(check), "no mid-stream state_check handed to the router"
    assert await check() is False
    await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
    assert await check() is True, "mid-stream check does not observe a live kill-switch"
    monkeypatch.setattr(gm, "REDIS_CLIENT", _BrokenRedis())
    assert await check() is False, "mid-stream check now fails CLOSED (behaviour changed)"

async def test_midstream_kill_halts_real_router_loop_without_leaking_remainder(env):
    """Drive the REAL LLMRouter._stream_with_state_check with the REAL gateway-built
    state_check: after the kill, remaining tokens must never be emitted and the loop
    must raise the terminal kill sentinel."""
    from ai_mesh_gateway.llm_router import LLMRouter, _MidStreamKillSwitch
    check = await _drain_stream_and_get_check(env)

    class _Chunk:
        def __init__(self, i): self.i = i
        def model_dump(self):
            return {"id": f"chatcmpl-{self.i}", "object": "chat.completion.chunk",
                    "model": PRIMARY, "choices": [{"index": 0, "finish_reason": None,
                                                   "delta": {"content": f"tok{self.i}"}}]}

    killed_at = {"n": None}

    async def _upstream():
        for i in range(60):
            if i == 20:
                await env.state.set(f"kill_switch:default:model:{PRIMARY}", _ks())
                killed_at["n"] = i
            yield _Chunk(i)

    router = LLMRouter.__new__(LLMRouter)
    emitted = []
    with pytest.raises(_MidStreamKillSwitch):
        async for frame in router._stream_with_state_check(
                _upstream(), client_model=PRIMARY, state_check=check):
            emitted.append(frame)
    assert killed_at["n"] == 20
    assert emitted, "stream produced nothing at all"
    assert len(emitted) < 60, "stream ran to completion despite a mid-stream kill"
    assert "tok59" not in "".join(emitted), "post-kill content leaked to the client"

_SERVER = fakeredis.FakeServer()

def _apply_live_stubs():
    """Same singletons, installed by direct assignment for the uvicorn-hosted app.
    Called per live test so a monkeypatch teardown from an ASGI test cannot strand it."""
    sync_r = fakeredis.FakeStrictRedis(server=_SERVER, decode_responses=True)
    sync_r.set(f"auth:apikey:{KEYHASH}", json.dumps(_auth()))

    async def _get_redis(self):
        return fakeredis.aioredis.FakeRedis(server=_SERVER, decode_responses=True)
    gw_middleware.AuthMiddleware._get_redis = _get_redis
    live_state = fakeredis.aioredis.FakeRedis(server=_SERVER, decode_responses=True)
    for name, val in _singletons(_cfg(), live_state, None, None, None).items():
        setattr(gm, name, val)
    with contextlib.suppress(Exception):
        gm.app.router.on_startup.clear(); gm.app.router.on_shutdown.clear()
    return sync_r

@pytest.fixture(scope="module")
def live_url():
    _apply_live_stubs()
    sk = socket.socket(); sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]; sk.close()
    server = uvicorn.Server(uvicorn.Config(gm.app, host="127.0.0.1", port=port,
                                           log_level="error", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    assert getattr(server, "started", False), "uvicorn live server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True; th.join(timeout=5)

async def test_circuit_breaker_open_without_fallback_returns_503(monkeypatch):
    """CB OPEN with no ModelState fallback → 503 circuit_breaker_open (not silent)."""
    from ai_mesh_gateway.circuit_breaker import CircuitBreaker, CircuitState

    async with _env_ctx(monkeypatch) as env:
        cb = CircuitBreaker(env.state, error_threshold=0.5, min_requests=1, cooldown_seconds=120)
        # Force OPEN without waiting for error window
        await env.state.set("circuit:state:gpt-4o-mini", CircuitState.OPEN.value)
        await env.state.set("circuit:open_at:gpt-4o-mini", str(time.time()))
        monkeypatch.setattr(gm, "CIRCUIT_BREAKER", cb)
        with pytest.raises(openai.APIStatusError) as ei:
            await env.client.chat.completions.create(model=PRIMARY, messages=MSG)
        assert ei.value.status_code == 503
        body = ei.value.response.json()
        err = body.get("error") or body
        assert err.get("code") in ("circuit_breaker_open", "kill_switch_active")
        assert UPSTREAM == []


async def test_circuit_breaker_open_with_model_state_fallback_reroutes(monkeypatch):
    """CB OPEN + ModelState.fallback_model → silent 200 on fallback (upstream proves ALT)."""
    from ai_mesh_gateway.circuit_breaker import CircuitBreaker, CircuitState

    async with _env_ctx(monkeypatch) as env:
        await env.state.set(
            f"model_state:default:{PRIMARY}",
            json.dumps({
                "status": "active",
                "action": "reroute",
                "fallback_model": ALT,
                "risk_score": 0,
                "threshold": 80,
            }),
        )
        cb = CircuitBreaker(env.state, error_threshold=0.5, min_requests=1, cooldown_seconds=120)
        await env.state.set(f"circuit:state:{PRIMARY}", CircuitState.OPEN.value)
        await env.state.set(f"circuit:open_at:{PRIMARY}", str(time.time()))
        monkeypatch.setattr(gm, "CIRCUIT_BREAKER", cb)
        r = await env.client.chat.completions.create(model=PRIMARY, messages=MSG)
        assert r.choices[0].message.content
        assert [b["model"] for b in UPSTREAM] == [ALT]


async def test_live_killswitch_over_a_real_socket(live_url):
    """Real TCP socket, real chunked transfer. (a) A killed model on stream=true yields a
    503 JSON envelope with no SSE framing and no [DONE] — nothing partial is committed to
    the wire. (b) Switched to reroute, the stream frames correctly end-to-end through the
    stock SDK and the UPSTREAM body proves the FALLBACK model served it."""
    sync_r = _apply_live_stubs()
    UPSTREAM.clear()
    key = f"kill_switch:default:model:{PRIMARY}"
    hdr = {"Authorization": f"Bearer {T.API_KEY}"}
    c = openai.AsyncOpenAI(base_url=f"{live_url}/v1", api_key=T.API_KEY, max_retries=0)
    try:
        sync_r.set(key, _ks())
        async with httpx.AsyncClient(base_url=live_url, timeout=10, headers=hdr) as rc:
            r = await rc.post("/v1/chat/completions",
                              json={"model": PRIMARY, "stream": True, "messages": MSG})
        assert r.status_code == 503
        assert "application/json" in r.headers.get("content-type", "")
        assert "data:" not in r.text and "[DONE]" not in r.text
        # over real HTTP the compat shim nests the flat envelope under `error`
        assert (r.json().get("error") or {}).get("code") == "kill_switch_active"
        assert UPSTREAM == []

        sync_r.set(key, _ks("reroute", ALT))
        stream = await c.chat.completions.create(
            model=PRIMARY, messages=[{"role": "user", "content": "stream"}], stream=True)
        parts = [ch.choices[0].delta.content or "" async for ch in stream
                 if ch.choices and ch.choices[0].delta]
        assert "".join(parts) == "Hello streaming world."
        assert [b["model"] for b in UPSTREAM] == [ALT]
    finally:
        await c.close(); sync_r.delete(key)
