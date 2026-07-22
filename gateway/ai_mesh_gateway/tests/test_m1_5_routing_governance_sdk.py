"""MODULE 1.5 — Multi-Model Governance & AI Mesh Routing, via the STOCK openai SDK.

Spec: docs/MODULE1_AI_MESH_FIREWALL.md §1.5. Drives the REAL app behind ASGITransport
with the UNMODIFIED openai SDK and asserts on what the UPSTREAM STUB ACTUALLY RECEIVED
— a control that "looks right" in the response but never changed the upstream call has
not run. The real LLMRouter scoring code is bound onto the mock router (_real_router),
so the arithmetic under test is production code. xfail(strict) == PROVEN gap
(M15-01..06); run with --runxfail to see the evidence.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py -q -p no:cacheprovider
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import httpx
import openai
import pytest
import pytest_asyncio

from ai_mesh_gateway.llm_router import LLMRouter
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

ORG = "org-mesh-routing"

# Multi-provider catalogue, separable on every routing dimension: cheapest llama
# (0.02/1k) · fastest gemini (400ms) · lowest-risk/only-restricted/only-hipaa claude.
def _m(name, mid, provider, risk, cost, sla, prio, sens, tags, key=True):
    return {"model_name": name, "model_id": mid, "provider": provider, "is_active": True,
            "api_key_set": key, "risk_score": risk, "cost_per_1k_input_tokens": cost,
            "latency_sla_ms": sla, "routing_priority": prio,
            "data_sensitivity_level": sens, "compliance_tags": tags}

CATALOGUE = [
    _m("gpt-4o-mini", "gpt-4o-mini", "openai", 0.20, 0.15, 800, 5, "public", []),
    _m("claude-3-5-sonnet", "anthropic/claude-3-5-sonnet", "anthropic", 0.05, 3.00,
       4000, 9, "restricted", ["hipaa", "gdpr"]),
    _m("gemini-1-5-flash", "google/gemini-1.5-flash", "google", 0.30, 0.05, 400, 3,
       "public", ["gdpr"]),
    _m("llama-3-70b", "meta/llama-3-70b", "ollama", 0.40, 0.02, 2500, 1, "public", [],
       key=False),
]
ALL_NAMES = [m["model_name"] for m in CATALOGUE]
_W = ("risk_weight", "cost_weight", "latency_weight", "priority_weight")

# A model that exists in the SHARED LiteLLM router but belongs to ANOTHER tenant.
FOREIGN_MODEL = {"id": "peer-org-private-gpt5", "object": "model",
                 "created": 1704067200, "owned_by": "some-other-org"}

def _routing_config(**over) -> dict:
    cfg = dict(T.TEST_CONFIG)
    cfg["routing_enabled"] = True
    cfg["output_policy_enabled"] = False  # separate capability; would call control plane
    cfg.update(over)
    return cfg

class Upstream:
    """Records exactly what the gateway asked the upstream to serve."""

    def __init__(self, response_factory=None):
        self.bodies: list[dict] = []
        self._factory = response_factory or _openai_shaped

    async def acompletion(self, body, redacted_prompt=None, **_kw):
        self.bodies.append(json.loads(json.dumps(body, default=str)))
        return 200, self._factory(body)

    @property
    def served_model(self) -> str:
        assert self.bodies, "upstream was never called"
        return self.bodies[-1]["model"]

def _openai_shaped(body) -> dict:
    return {"id": "chatcmpl-m15", "object": "chat.completion", "created": 1700000000,
            "model": body.get("model", ""), "usage": {"prompt_tokens": 10,
            "completion_tokens": 5, "total_tokens": 15},
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}]}

def _anthropic_flavoured(body) -> dict:
    """litellm-normalised Anthropic/Gemini response still carrying provider junk."""
    call = {"id": "toolu_01ABC", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}}
    return {
        "id": "chatcmpl-anthropic-1", "object": "chat.completion", "created": 1700000000,
        "model": "anthropic/claude-3-5-sonnet-20241022", "provider": "Anthropic",
        "citations": ["https://internal.example/doc"],
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "native_finish_reason": "tool_use",
                     "message": {"role": "assistant", "content": None, "tool_calls": [call],
                                 "provider_specific_fields": {"native_finish_reason": "tool_use"}}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19,
                  "cost": 0.00042, "is_byok": True},
    }

def _real_router(active_names=None) -> LLMRouter:
    """Real LLMRouter scoring code without litellm/network init."""
    r = LLMRouter.__new__(LLMRouter)
    r._config = {"litellm_default_model": ""}
    r._active_model_names = list(ALL_NAMES if active_names is None else active_names)
    r._qualified_model_names = set(r._active_model_names)
    return r

async def _build(monkeypatch, *, org_slug=ORG, allowed_models=None, config=None,
                 upstream=None, active_names=None, connected=True):
    # connected=True == REGISTERED with the control plane. main.py:7563 `if not
    # AGENT_ID or not CONFIG["backend_url"]` short-circuits straight upstream and
    # skips the ENTIRE 1.5 block (:7796-8245); connected=False proves that (M15-02/06).
    from ai_mesh_gateway import main as gm, middleware as gw_middleware
    from ai_mesh_gateway.scanner import InputScanner

    payload = dict(T._auth_payload(), org_slug=org_slug, organization_id=ORG,
                   allowed_models=list(ALL_NAMES if allowed_models is None else allowed_models))
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await auth_redis.set(
        f"auth:apikey:{hashlib.sha256(T.API_KEY.encode('utf-8')).hexdigest()}",
        json.dumps(payload))

    async def _get_redis(self):
        return auth_redis
    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)

    cfg = dict(config or _routing_config())
    if connected:
        cfg["backend_url"] = "http://control-plane.invalid"
    cs = MagicMock()
    cs.get_config = MagicMock(return_value=dict(cfg))
    cs.get_model_routing = MagicMock(return_value=[dict(m) for m in CATALOGUE])
    cs.reload_models_now = AsyncMock()
    cs.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})

    up = upstream or Upstream()
    real = _real_router(active_names)
    lr = MagicMock()
    lr.acompletion = AsyncMock(side_effect=up.acompletion)
    lr.acompletion_stream = T._fake_stream
    lr.aembedding = AsyncMock(side_effect=T._fake_embedding)
    lr.get_model_list = MagicMock(return_value=(
        [{"id": n, "object": "model", "created": 1704067200, "owned_by": "openai"}
         for n in ALL_NAMES] + [dict(FOREIGN_MODEL)]))
    lr.estimate_prompt_tokens = MagicMock(return_value=500)
    lr.adjudicate_model_selection = real.adjudicate_model_selection
    lr.resolve_runtime_selection = real.resolve_runtime_selection

    # _policy_check_cached: the policy engine is a separate capability (and would
    # HTTP the control plane); (200, {}) is its "no policy matched" contract.
    for attr, val in {
        "CONFIG": dict(cfg), "CONFIG_SYNC": cs, "LLM_ROUTER": lr,
        "INPUT_SCANNER": InputScanner(thread_pool_size=2),
        "AGENT_ID": "agent-m15" if connected else None,
        "_emit_telemetry": lambda **_k: None,
        "_audit_fire_and_forget": lambda **_k: None,
        "_policy_check_cached": lambda *a, **k: (200, {}),
        **dict.fromkeys(("POLICY_SYNC", "RATE_LIMITER", "CIRCUIT_BREAKER",
                         "REDIS_CLIENT", "TELEMETRY", "OUTPUT_GUARD")),
    }.items():
        monkeypatch.setattr(gm, attr, val)
    # adjudicator off => assert the DETERMINISTIC weighted arithmetic, not an LLM
    monkeypatch.setenv("ROUTING_ADJUDICATOR_ALWAYS", "false")

    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY, max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=gm.app),
                                      base_url="http://testserver"))
    return client, up, auth_redis

@pytest_asyncio.fixture()
async def build(monkeypatch):
    """Factory for app variants; closes every client/redis it hands out."""
    made = []

    async def _f(**kw):
        client, up, redis = await _build(monkeypatch, **kw)
        made.append((client, redis))
        return client, up
    yield _f
    for _c, _r in made:
        await _c.close()
        await _r.aclose()

@pytest_asyncio.fixture()
async def routed(build):
    """Default connected app with the full catalogue."""
    return await build()

async def _chat(client, model="auto", prefs=None, **extra):
    """Stock SDK call; governance fields ride in extra_body as an integrator must."""
    extra_body = dict(extra.pop("extra_body", {}) or {})
    if prefs is not None:
        extra_body["routing_preferences"] = prefs
    return await client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "quarterly plan"}],
        extra_body=extra_body or None, **extra)

# ═════════ A. Model catalogue: shape, org scoping, entitlement, enumeration ═════════
@pytest.mark.asyncio
async def test_models_list_is_org_scoped_and_sdk_parseable(routed):
    client, _ = routed
    page = await client.models.list()
    ids = [m.id for m in page.data]
    assert ids, "catalogue is empty"
    assert all(m.object == "model" for m in page.data)
    assert set(ids) == set(ALL_NAMES), ids
    assert FOREIGN_MODEL["id"] not in ids, "cross-tenant model leaked into /v1/models"
    assert all(m.owned_by == ORG for m in page.data)

@pytest.mark.asyncio
async def test_models_list_is_empty_when_key_has_no_org_slug(build):
    """FIXED (I-11). ``if org_slug and CONFIG_SYNC`` had no else-branch, so an
    org-less key fell through to LLM_ROUTER.get_model_list() — the SHARED LiteLLM
    catalogue config_sync builds by merging EVERY org's deployments — and
    enumerated other tenants' model names. Now fails closed to an empty list."""
    client, _ = await build(org_slug="")
    ids = [m.id for m in (await client.models.list()).data]
    assert FOREIGN_MODEL["id"] not in ids, (
        f"cross-tenant enumeration: org-less key sees {ids}")

@pytest.mark.asyncio
async def test_models_list_is_scoped_to_the_keys_entitlement(build):
    """FIXED (I-21). The catalogue was ORG-scoped but never KEY-scoped, so a key
    entitled to one model still saw the org's whole fleet. Containment always held
    (routing_allowed_models is the hard filter); this aligns what a key can SEE with
    what it can USE."""
    client, _ = await build(allowed_models=["gpt-4o-mini"])
    ids = [m.id for m in (await client.models.list()).data]
    assert set(ids) == {"gpt-4o-mini"}, (
        f"key entitled to gpt-4o-mini only, but models.list() returned {ids}")

@pytest.mark.asyncio
async def test_models_retrieve_does_not_leak_foreign_model(routed):
    client, _ = routed
    with pytest.raises(openai.NotFoundError) as ei:
        await client.models.retrieve(FOREIGN_MODEL["id"])
    assert ei.value.status_code == 404

@pytest.mark.asyncio
async def test_models_retrieve_known_matches_list_entry(routed):
    client, _ = routed
    m = await client.models.retrieve("claude-3-5-sonnet")
    assert m.id == "claude-3-5-sonnet" and m.object == "model"

@pytest.mark.asyncio
async def test_unknown_model_is_notfounderror_not_500(routed):
    client, up = routed
    with pytest.raises(openai.NotFoundError) as ei:
        await _chat(client, model="gpt-99-omniscient")
    assert ei.value.status_code == 404
    assert (ei.value.body or {}).get("code") == "model_not_configured"
    assert not up.bodies, "unknown model still reached the upstream"

# ═════════ B. Dimensional routing — assert on the UPSTREAM body ═════════
@pytest.mark.asyncio
async def test_sensitive_prompt_is_rerouted_away_from_requested_model(routed):
    client, up = routed
    await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert up.served_model == "claude-3-5-sonnet", (
        f"restricted data served by {up.served_model}")

@pytest.mark.asyncio
async def test_sensitivity_floor_overrides_the_cost_preference(routed):
    """Cost-dominant weights take the cheap model when PUBLIC, but the SAME
    weights on a RESTRICTED request must be overruled by the sensitivity floor —
    proving sensitivity caused the reroute, not claude winning on score."""
    client, up = routed
    cheap = {**dict.fromkeys(_W, 0.0), "cost_weight": 1.0}
    await _chat(client, model="gpt-4o-mini", prefs=dict(cheap, data_sensitivity="public"))
    assert up.served_model == "llama-3-70b", up.served_model
    await _chat(client, model="gpt-4o-mini", prefs=dict(cheap, data_sensitivity="restricted"))
    assert up.served_model == "claude-3-5-sonnet", (
        f"cost preference overrode the sensitivity floor: {up.served_model}")

@pytest.mark.parametrize("prefs,expect", [
    ({"cost_weight": 1.0}, "llama-3-70b"),                        # cheapest 0.02/1k
    ({"latency_weight": 1.0, "latency_budget_ms": 500}, "gemini-1-5-flash"),  # 400ms SLA
    ({"risk_weight": 1.0, "model_risk_score": 0.9}, "claude-3-5-sonnet"),     # risk 0.05
])
@pytest.mark.asyncio
async def test_each_weight_dimension_routes_to_its_own_winner(routed, prefs, expect):
    """Each governance dimension must actually be consumed by the scorer: the weight
    is set to 1.0 and every other weight to 0.0, so the winner is unambiguous."""
    client, up = routed
    await _chat(client, prefs={**dict.fromkeys(_W, 0.0), **prefs})
    assert up.served_model == expect, f"{prefs} routed to {up.served_model}"

@pytest.mark.asyncio
async def test_compliance_tag_is_a_hard_filter(routed):
    client, up = routed
    await _chat(client, model="gpt-4o-mini",
                prefs={"compliance_requirements": ["hipaa"], "cost_weight": 1.0})
    assert up.served_model == "claude-3-5-sonnet"

@pytest.mark.asyncio
async def test_unsatisfiable_compliance_fails_closed_403(routed):
    client, up = routed
    with pytest.raises(openai.PermissionDeniedError) as ei:
        await _chat(client, prefs={"compliance_requirements": ["pci-dss"]})
    body = ei.value.body or {}
    assert body.get("code") == "compliance_routing_unsatisfiable", body
    assert not up.bodies, "non-compliant request still reached an upstream model"

@pytest.mark.asyncio
async def test_key_entitlement_is_a_hard_routing_filter(build):
    """A key entitled only to gemini must not be routed onto claude even when the
    org owns claude and the weights favour it."""
    client, up = await build(allowed_models=["gemini-1-5-flash"])
    await _chat(client, prefs={**dict.fromkeys(_W, 0.0),
                               "risk_weight": 1.0, "model_risk_score": 0.9})
    assert up.served_model == "gemini-1-5-flash", (
        f"key entitlement bypassed: served {up.served_model}")

# FINDING M15-02 FIXED (I-02): the standalone short-circuit now gates on
# ROUTING-CATALOGUE availability, not on AGENT_ID, so an unregistered worker with a
# warm catalogue runs the full §1.5 governance block. xfail removed — this passes.
@pytest.mark.asyncio
async def test_m15_02_unregistered_gateway_still_enforces_routing_governance(build):
    """§1.5 must not depend on control-plane registration — the unregistered state
    is the one operators are least likely to notice."""
    client, up = await build(connected=False)
    with pytest.raises(openai.PermissionDeniedError):
        await _chat(client, prefs={"compliance_requirements": ["pci-dss"]})
    await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert up.served_model == "claude-3-5-sonnet", (
        f"unregistered gateway served restricted data on {up.served_model}")

@pytest.mark.asyncio
async def test_compliance_enforced_even_when_routing_disabled(build):
    """FIX-1.5a: routing off means no adjudicator, so the chosen model must still
    be checked against the request compliance/sensitivity."""
    client, up = await build(config=_routing_config(routing_enabled=False))
    with pytest.raises(openai.PermissionDeniedError):
        await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert not up.bodies

# ═════════ C. Honesty of the governance record ═════════
@pytest.mark.asyncio
async def test_routing_metadata_is_visible_to_the_sdk_caller(routed):
    client, _ = routed
    r = await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    zs = getattr(r, "zeroshield", None)
    assert isinstance(zs, dict), f"no zeroshield envelope: {zs!r}"
    routing = zs.get("routing")
    assert isinstance(routing, dict), f"no zeroshield.routing: {sorted(zs)}"
    assert routing.get("routing_enabled") is True, routing
    assert routing.get("data_sensitivity") == "restricted", routing
    assert routing.get("rerouted") is True, routing
    assert routing.get("routed_model") == "claude-3-5-sonnet", routing
    assert routing.get("original_model") == "gpt-4o-mini", routing
    assert routing.get("routing_score") is not None and routing.get("candidate_scores")

@pytest.mark.xfail(strict=True, reason=(
    "FINDING M15-01 (HIGH): main.py:9643 overwrites llm_resp[model] with "
    "route_metadata[original_model] - the REQUESTED model. On a reroute the "
    "OpenAI-visible `model` names a model that did not serve the request "
    "(response.model==gpt-4o-mini while upstream got claude-3-5-sonnet). "))
@pytest.mark.asyncio
async def test_response_model_field_reports_the_model_that_actually_served(routed):
    """response.model is an SDK client's only per-response record of WHICH model
    ran; echoing the request misattributes every SDK-built cost/audit ledger."""
    client, up = routed
    r = await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert up.served_model == "claude-3-5-sonnet"
    assert r.model == up.served_model, (
        f"response.model={r.model!r} but upstream served {up.served_model!r}")

@pytest.mark.asyncio
async def test_inactive_selection_is_remapped_not_silently_served(build):
    """resolve_runtime_selection: a scored winner with no live deployment must be
    remapped, never pretended-served."""
    client, up = await build(active_names=[n for n in ALL_NAMES if "claude" not in n])
    await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert up.served_model != "claude-3-5-sonnet", (
        "gateway routed to a model with no live deployment")

# ═════════ D. Provider heterogeneity — non-OpenAI upstream must normalise ═════════
@pytest.mark.asyncio
async def test_anthropic_shaped_response_parses_as_openai_chatcompletion(build):
    """An Anthropic-flavoured upstream (tool_use finish reason, provider
    passthrough, BYOK cost) must reach the SDK as a clean ChatCompletion:
    finish_reason/usage/tool_calls intact, provider+cost topology stripped."""
    up = Upstream(response_factory=_anthropic_flavoured)
    client, up = await build(upstream=up)
    r = await client.chat.completions.create(model="claude-3-5-sonnet",
        messages=[{"role": "user", "content": "weather in Paris?"}],
        tools=[{"type": "function", "function": {"name": "get_weather", "description": "w",
            "parameters": {"type": "object", "properties": {}}}}])
    choice = r.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.tool_calls[0].function.name == "get_weather"
    assert json.loads(choice.message.tool_calls[0].function.arguments)["city"] == "Paris"
    assert r.usage.total_tokens == 19
    raw = r.model_dump()
    assert "provider" not in raw, "upstream provider identity leaked"
    assert "cost" not in (raw.get("usage") or {}), "BYOK cost basis leaked"
    assert "claude-3-5-sonnet-20241022" not in json.dumps(raw, default=str), (
        "raw upstream provider model id leaked to the client")

# FINDING M15-06 FIXED (I-12, via the I-02 fix): an unregistered worker with a warm
# catalogue now returns through the connected branch, which applies the provider-
# topology scrub. xfail removed — this passes.
@pytest.mark.asyncio
async def test_m15_06_unregistered_gateway_still_scrubs_provider_topology(build):
    """Provider heterogeneity must not become provider DISCLOSURE."""
    up = Upstream(response_factory=_anthropic_flavoured)
    client, up = await build(upstream=up, connected=False)
    r = await client.chat.completions.create(
        model="claude-3-5-sonnet", messages=[{"role": "user", "content": "hi"}])
    raw = r.model_dump()
    assert "provider" not in raw, "upstream provider identity leaked"
    assert "citations" not in raw, "upstream citations leaked"
    assert "cost" not in (raw.get("usage") or {}), "BYOK cost basis leaked"
    assert "claude-3-5-sonnet-20241022" not in json.dumps(raw, default=str), (
        "raw upstream provider model id leaked")

@pytest.mark.asyncio
async def test_every_catalogue_provider_serves_a_parseable_completion(build):
    client, up = await build()
    for name in ALL_NAMES:
        r = await client.chat.completions.create(
            model=name, messages=[{"role": "user", "content": "ping"}],
            extra_body={"routing_preferences": {"enable_routing": False}})
        assert r.choices[0].message.content == "ok"
        assert r.object == "chat.completion"
    assert [b["model"] for b in up.bodies] == ALL_NAMES, (
        f"per-model dispatch diverged: {[b['model'] for b in up.bodies]}")

# ═════════ E. Client-controlled governance inputs ═════════
@pytest.mark.asyncio
async def test_client_disabling_routing_cannot_escape_the_governance_floor(routed):
    """enable_routing=false IS honoured with no privilege check
    (_extract_chat_routing_preferences:2575-2584) — so the attack is: mesh off, keep
    the cheap public model, send restricted data. FIX-1.5a (main.py:8089) must fail
    CLOSED. Asserts the security property, not the mechanism."""
    client, up = routed
    try:
        await _chat(client, model="gpt-4o-mini",
                    prefs={"data_sensitivity": "restricted", "enable_routing": False})
    except openai.PermissionDeniedError as exc:
        assert (exc.body or {}).get("code") == "compliance_routing_unsatisfiable", exc.body
        assert not up.bodies, "blocked request still reached an upstream model"
        return
    assert up.served_model == "claude-3-5-sonnet", (
        f"client disabled routing and was served restricted data on {up.served_model}")

@pytest.mark.asyncio
async def test_client_disabled_routing_is_disclosed_as_an_override(routed):
    client, _ = routed
    with pytest.raises(openai.PermissionDeniedError) as ei:
        await _chat(client, model="gpt-4o-mini",
                    prefs={"data_sensitivity": "restricted", "enable_routing": False})
    routing = (ei.value.response.json().get("zeroshield") or {}).get("routing") or {}
    assert routing.get("routing_override") is False, routing
    assert routing.get("org_routing_enabled") is True, routing
    assert routing.get("routing_enabled") is False, routing

@pytest.mark.asyncio
async def test_unknown_sensitivity_value_fails_closed(routed):
    """_req_sensitivity_level: an unrecognised value must be treated as MOST
    restrictive, not silently downgraded to public."""
    client, up = routed
    await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "topsecret"})
    assert up.served_model == "claude-3-5-sonnet", (
        f"unknown sensitivity fell open to {up.served_model}")

@pytest.mark.asyncio
async def test_hostile_routing_weights_do_not_500(routed):
    """Caller weights are unvalidated input: negative / huge / wrong-type must
    degrade, never crash or invert the scorer onto the worst model."""
    client, up = routed
    for prefs in ({"risk_weight": -5, "cost_weight": 1e308},
        {"risk_weight": "nope", "latency_budget_ms": "soon"},
        {"weights": {"risk": None, "cost": [1, 2]}},
        {"cost_weight": "1e999", "latency_budget_ms": -1},
        {"data_sensitivity": {"nested": "dict"}, "compliance_requirements": "hipaa"},
    ):
        r = await _chat(client, prefs=prefs)
        assert r.choices[0].message.content == "ok"
    assert all(b["model"] in ALL_NAMES for b in up.bodies)

@pytest.mark.asyncio
async def test_routing_never_selects_a_model_outside_the_org_catalogue(routed):
    client, up = routed
    for ds in ("public", "internal", "confidential", "restricted"):
        for w in _W:
            await _chat(client, prefs={"data_sensitivity": ds, w: 1.0})
    assert up.bodies
    assert all(b["model"] in ALL_NAMES for b in up.bodies), (
        f"escaped catalogue: {sorted({b['model'] for b in up.bodies})}")
