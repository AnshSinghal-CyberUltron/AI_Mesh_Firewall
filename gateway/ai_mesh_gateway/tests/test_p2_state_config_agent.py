"""PHASE-2 state-config-agent adversarial repros.

Mandate: CHALLENGE "cross-tenant isolation holds" + "single /v1/responses route"
with a fakeredis-backed app, and hunt store-layer edges the parallel triage skipped.

Convention (phase2): a CONFIRMED defect is xfail(strict=True) (XFAIL=real, XPASS=fix);
a test that should PASS (proving a claimed defect is a false-positive / the claim holds)
is left UNmarked (PASS=claim correct, no defect).

Run: cd gateway && .venv/bin/python -m pytest ai_mesh_gateway/tests/_p2_state-config-agent.py -q -rxX
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import openai
import pytest
import pytest_asyncio

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.tests import test_openai_sdk_compat as T


# ── a SECOND tenant's API key seeded into the SAME auth redis ──────────────────
API_KEY_B = "zs_test_sdk_compat_ORGB_0123456789abc"
ORG_A = "org-sdk-compat"          # T._auth_payload()'s org
ORG_B = "org-sdk-compat-TENANT-B"


def _auth_payload_b() -> dict:
    p = dict(T._auth_payload())
    p["key_id"] = "550e8400-e29b-41d4-a716-446655440099"
    p["prefix"] = API_KEY_B[:8]
    p["organization_id"] = ORG_B
    return p


async def _capf(cap):
    async def _cap(body, redacted_prompt=None, **_kw):
        cap.clear()
        cap.update(body)
        return 200, {
            "id": "chatcmpl-x", "object": "chat.completion", "created": 1700000000,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return _cap


@pytest_asyncio.fixture()
async def stateful(monkeypatch):
    """Redis-backed app so ResponseStore persists; capturing upstream. We hold a handle
    on the auth_redis so we can seed a SECOND tenant key (org B) into the same store."""
    state_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=state_redis)
    # seed org B's key alongside org A's (already seeded by _make_sdk_app)
    kb = hashlib.sha256(API_KEY_B.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{kb}", json.dumps(_auth_payload_b()))
    cap = {}
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=await _capf(cap))
    yield app, state_redis, cap
    await state_redis.aclose()
    await auth_redis.aclose()


def _client(app, key=T.API_KEY):
    return openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=key,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
        max_retries=0,
    )


def _raw(app, key=T.API_KEY):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        headers={"Authorization": f"Bearer {key}"},
    )


# ════════════════ (a) CROSS-TENANT ISOLATION — these MUST hold (PASS) ════════════════

@pytest.mark.asyncio
async def test_cross_tenant_retrieve_404(stateful):
    """org A creates store=true; org B retrieve(id) MUST 404 (no cross-tenant replay)."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    cb = _client(app, API_KEY_B)
    try:
        created = await ca.responses.create(model="gpt-4o-mini", input="secret A", store=True)
        # org A can read its own
        own = await ca.responses.retrieve(created.id)
        assert own.id == created.id
        # org B must NOT
        with pytest.raises(openai.NotFoundError) as exc:
            await cb.responses.retrieve(created.id)
        assert exc.value.status_code == 404
    finally:
        await ca.close()
        await cb.close()


@pytest.mark.asyncio
async def test_cross_tenant_previous_response_id_404(stateful):
    """org A creates store=true; org B passing previous_response_id=<A's id> MUST 404
    (cannot continue another tenant's stored conversation)."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        first = await ca.responses.create(model="gpt-4o-mini", input="My name is Ada (A).", store=True)
    finally:
        await ca.close()
    # org B tries to chain off A's stored turn
    async with _raw(app, API_KEY_B) as rc:
        resp = await rc.post("/v1/responses", json={
            "model": "gpt-4o-mini", "input": "What was the name?",
            "previous_response_id": first.id, "store": True,
        })
    assert resp.status_code == 404, f"cross-tenant prev_id leaked: status={resp.status_code} body={resp.text[:300]}"


@pytest.mark.asyncio
async def test_cross_tenant_delete_404_and_no_destruction(stateful):
    """org B delete(A's id) MUST 404 AND MUST NOT destroy A's record (no cross-tenant
    delete-as-DoS). Verify A can still read it afterward."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    cb = _client(app, API_KEY_B)
    try:
        created = await ca.responses.create(model="gpt-4o-mini", input="keep me", store=True)
        with pytest.raises(openai.NotFoundError):
            await cb.responses.delete(created.id)
        # A's record survived B's delete attempt
        still = await ca.responses.retrieve(created.id)
        assert still.id == created.id
    finally:
        await ca.close()
        await cb.close()


@pytest.mark.asyncio
async def test_cross_tenant_input_items_404(stateful):
    """org B listing input_items of A's response MUST 404."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        created = await ca.responses.create(model="gpt-4o-mini", input="items A", store=True)
    finally:
        await ca.close()
    async with _raw(app, API_KEY_B) as rc:
        resp = await rc.get(f"/v1/responses/{created.id}/input_items")
    assert resp.status_code == 404, f"cross-tenant input_items leaked: {resp.status_code} {resp.text[:200]}"


# ════════════════ (b) ROUTE INTROSPECTION (D1) — MUST hold (PASS) ════════════════

@pytest.mark.asyncio
async def test_exactly_one_post_v1_responses_route(stateful):
    """D1: exactly one POST /v1/responses route registered (no duplicate handler)."""
    app, _redis, _cap = stateful
    post_routes = [
        r for r in app.routes
        if getattr(r, "methods", None) and "POST" in r.methods and getattr(r, "path", "") == "/v1/responses"
    ]
    assert len(post_routes) == 1, f"expected 1 POST /v1/responses, found {len(post_routes)}: {[getattr(r,'path','') for r in post_routes]}"


# ════════════════ (c) STORE DURABILITY across a fresh _ResponseStore instance ════════════════

@pytest.mark.asyncio
async def test_store_survives_fresh_store_instance(stateful):
    """A new _ResponseStore over the SAME redis ('worker restart') still resolves the
    record — durability is in redis, not instance state."""
    app, state_redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        created = await ca.responses.create(model="gpt-4o-mini", input="durable", store=True)
    finally:
        await ca.close()
    from ai_mesh_gateway.responses_store import ResponseStore
    fresh = ResponseStore(state_redis)
    obj = await fresh.get_response(ORG_A, created.id)
    assert obj is not None and obj.get("id") == created.id
    # and a fresh store for org B still cannot read it
    assert await fresh.get_response(ORG_B, created.id) is None


# ════════════════ NEW HUNT — store-layer edges the triage may have skipped ════════════════

@pytest.mark.asyncio
async def test_org_none_collapse_isolation(stateful):
    """ADVERSARIAL: the store keys on str(org_id). org A is a real string. Probe whether
    a request whose org resolves to None (key 'responses:None:<id>') could ever collide
    with a real org. Here we assert org A's record is NOT readable under org_id=None via a
    fresh store (the None namespace is distinct). PASS = no None-collapse leak."""
    app, state_redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        created = await ca.responses.create(model="gpt-4o-mini", input="x", store=True)
    finally:
        await ca.close()
    from ai_mesh_gateway.responses_store import ResponseStore
    fresh = ResponseStore(state_redis)
    assert await fresh.get_response(None, created.id) is None


@pytest.mark.xfail(strict=True, reason="P2-STORE-org-typecollision (probe): ResponseStore._key f-strings org_id WITHOUT a separator-safe encoding and _load_record compares str(org_id). If two distinct org_id VALUES stringify equally (e.g. int 7 vs str '7', from heterogeneous control-plane payloads), tenant B could read tenant A. This asserts the store DISTINGUISHES org_id 7 (int) from '7' (str); XFAIL means it does NOT (collision).")
@pytest.mark.asyncio
async def test_store_org_id_int_vs_str_collision(stateful):
    """Direct store-layer probe: save under int org 7, read under str '7'. OpenAI
    organization ids are strings; if the control plane ever emits an int org_id for one
    tenant and a str for another with the same digits, the str()-keyed store collides."""
    app, state_redis, _cap = stateful
    from ai_mesh_gateway.responses_store import ResponseStore
    store = ResponseStore(state_redis)
    obj = {"id": "resp_intkeytest", "object": "response", "output_text": "tenant-int-7"}
    await store.save(7, obj, replay_messages=[])
    # a DIFFERENT tenant whose org_id is the string "7" must NOT read tenant int-7's data
    leaked = await store.get_response("7", "resp_intkeytest")
    assert leaked is None, f"int/str org_id collision: org '7' read org 7's record: {leaked}"


@pytest.mark.asyncio
async def test_redis_none_store_silent_noop_create_still_200(monkeypatch):
    """(c) flag: with REDIS_CLIENT=None, store=true is a SILENT no-op (ResponseStore.save
    early-returns). create() still 200s but the response is NOT retrievable. This documents
    the degraded-mode behavior; the real-redis assertion is the cross-tenant suite above.
    PASS here = create succeeds (no crash); the no-op is a needs_live Phase-7 concern."""
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cap = {}
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=await _capf(cap))
    client = _client(app, T.API_KEY)
    try:
        created = await client.responses.create(model="gpt-4o-mini", input="hi", store=True)
        assert created.id.startswith("resp")
        # retrieve a stored id under a None-redis store -> 404 (silent no-op proven)
        with pytest.raises(openai.NotFoundError):
            await client.responses.retrieve(created.id)
    finally:
        await client.close()
        await auth_redis.aclose()


# ════════════════ NEW HUNT — GET vs DELETE 401-vs-404 ordering & ttl/store-flag edges ════════════════

@pytest.mark.asyncio
async def test_retrieve_unknown_id_same_org_is_404_not_500(stateful):
    """A retrieve for a well-formed-but-unknown id in the caller's OWN org -> clean 404."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        with pytest.raises(openai.NotFoundError) as exc:
            await ca.responses.retrieve("resp_does_not_exist_0000")
        assert exc.value.status_code == 404
    finally:
        await ca.close()


@pytest.mark.xfail(strict=True, reason="P2-STORE-create-without-store-not-retrievable-but-prev_id-also-404 (probe LOW): a create() WITHOUT store=true returns a resp id but persists nothing, so previous_response_id=<that id> 404s. OpenAI ALSO requires store=true to chain, so this is conformant — XFAIL marker is a deliberate trip-wire: if it XPASSes the claim 'unstored ids are not chainable' is the real behavior and this should be dropped.")
@pytest.mark.asyncio
async def test_unstored_response_is_not_chainable(stateful):
    """Probe: create WITHOUT store, then try to chain. Expect 404 (not chainable). The
    xfail trip-wire flips if the gateway somehow lets an unstored id chain."""
    app, _redis, _cap = stateful
    ca = _client(app, T.API_KEY)
    try:
        unstored = await ca.responses.create(model="gpt-4o-mini", input="ephemeral")
    finally:
        await ca.close()
    async with _raw(app, T.API_KEY) as rc:
        resp = await rc.post("/v1/responses", json={
            "model": "gpt-4o-mini", "input": "continue", "previous_response_id": unstored.id})
    # 404 is the conformant outcome -> to make this an xfail trip-wire we assert the
    # NON-conformant 200 so XFAIL=conformant(404), XPASS=leak(200).
    assert resp.status_code == 200, f"unstored id WAS chainable (status={resp.status_code}) — that would be the defect"


# ════════════════ REACHABILITY of the int/str collision through the AUTH path ════════════════

@pytest.mark.asyncio
async def test_authpath_org_id_is_consistently_typed_per_tenant(monkeypatch):
    """REACHABILITY CHECK for P2-STORE-org-typecollision: the live cross-tenant leak only
    fires if two DIFFERENT tenants present org_ids that stringify equally (int 7 vs str '7').
    The control plane builds the auth payload as ``"organization_id": org.id`` (a Django PK =
    int) for EVERY key (models.py:739), so after JSON round-trip every tenant's org_id is an
    int. We assert an int org_id seeded into auth redis round-trips to an int org-scoped store
    key — i.e. the collision is LATENT (store-primitive weakness), not a live leak. PASS =
    org_id stays int through auth -> no two real tenants collide today."""
    state_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=state_redis)
    # reseed org A's key with an INTEGER organization_id (mirrors org.id from control)
    int_payload = dict(T._auth_payload())
    int_payload["organization_id"] = 4242  # int, exactly as Django PK serializes
    kh = hashlib.sha256(T.API_KEY.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{kh}", json.dumps(int_payload))
    cap = {}
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=await _capf(cap))
    client = _client(app, T.API_KEY)
    try:
        created = await client.responses.create(model="gpt-4o-mini", input="int-org", store=True)
        fetched = await client.responses.retrieve(created.id)
        assert fetched.id == created.id
    finally:
        await client.close()
    # the redis key MUST be the int-namespaced one (proves auth preserved int typing)
    keys = await state_redis.keys("responses:*")
    assert any(":4242:" in k for k in keys), f"expected int-namespaced key, got {keys}"
    assert not any(":None:" in k for k in keys)
    await state_redis.aclose()
    await auth_redis.aclose()


# ════════════════ x-request-id on store-endpoint 404s (shim coverage on store routes) ════════════════

@pytest.mark.asyncio
async def test_store_404_carries_x_request_id(stateful):
    """The OpenAI-compat shim must stamp x-request-id on the store endpoints' 404s too,
    so a customer's e.request_id is joinable on a missing/cross-tenant retrieve."""
    app, _redis, _cap = stateful
    async with _raw(app, T.API_KEY) as rc:
        resp = await rc.get("/v1/responses/resp_missing_xrid_probe")
    assert resp.status_code == 404
    assert resp.headers.get("x-request-id"), f"store 404 missing x-request-id; headers={dict(resp.headers)}"
