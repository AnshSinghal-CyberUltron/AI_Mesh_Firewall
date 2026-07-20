"""MODULE 1.1 — AI Gateway & Traffic Ingress, through the STOCK openai SDK.

Auth (user/service/agent keys), tenant isolation, key-scoped model allowlists,
action permissions, token budgets and rate limiting (DDoS-style AI traffic
shaping), observed exactly as a customer sees them via ``openai.AsyncOpenAI``
with base_url + api_key swapped.

Method: every control that MUTATES a request (token clamp, org binding) is
asserted against what the UPSTREAM STUB ACTUALLY RECEIVED. A 200 is not proof
a control ran. Header-spoofing cells additionally prove the spoof was DELIVERED,
so a pass means the gateway ignored it rather than that it was never sent.

Run: cd gateway && .venv/bin/python -m pytest \
       ai_mesh_gateway/tests/test_m1_1_ingress_sdk.py -q -p no:cacheprovider
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import pytest_asyncio

from ai_mesh_gateway.tests import test_openai_sdk_compat as T

ORG_A = "org-alpha"
ORG_B = "org-bravo"

# All >= 16 chars so they clear the format pre-check and are judged by the Redis
# lookup itself (KEY_SHORT deliberately does not).
KEY_ORG_A = "zs_m11_orgalpha_key_0123456789ab"
KEY_UNKNOWN = "zs_m11_unknown_key_0123456789abcd"
KEY_INACTIVE = "zs_m11_inactive_key_0123456789ab"
KEY_EXPIRED = "zs_m11_expired_key_00123456789ab"
KEY_CORRUPT = "zs_m11_corrupt_key_00123456789ab"
KEY_CHATONLY = "zs_m11_chatonly_key_0123456789ab"
KEY_SHORT = "zs_short"
KEY_WRONGPFX = "sk-proj-notazeroshieldkey12345678"

_SPOOF = {
    "X-Org-Id": ORG_B, "X-Organization": ORG_B, "X-Org-Slug": ORG_B,
    "X-Tenant": ORG_B, "X-Tenant-Id": ORG_B, "X-Gateway-Roles": "admin,owner",
    "X-User-ID": "999999", "X-Endpoint-ID": "999999",
}


def _payload(**over) -> dict:
    p = T._auth_payload()
    p.update({"org_slug": ORG_A, "organization_id": "orgid-alpha"})
    p.update(over)
    return p


def _hash(k: str) -> str:
    return hashlib.sha256(k.encode("utf-8")).hexdigest()


def _header_spy(app, sink: list):
    """Records the headers each request ACTUALLY carried."""
    async def _wrapped(scope, receive, send):
        if scope["type"] == "http":
            sink.append({k.decode("latin-1").lower(): v.decode("latin-1")
                         for k, v in scope.get("headers", [])})
        await app(scope, receive, send)

    return _wrapped


class Harness:
    """Handles on the stubbed gateway so tests can assert on server-side state."""
    def __init__(self, app, auth_redis, gm):
        self.app, self.auth_redis, self.gm = app, auth_redis, gm
        self.chat_bodies: list[dict] = []
        self.embed_bodies: list[dict] = []
        self.seen_headers: list[dict] = []

    @property
    def config_sync(self):
        return self.gm.CONFIG_SYNC

    def orgs_resolved(self) -> set:
        return {a.args[0] for a in self.config_sync.get_config.call_args_list if a.args}

    def set_org_config(self, **over):
        cfg = dict(T.TEST_CONFIG)
        cfg.update(over)
        self.config_sync.get_config.return_value = cfg
        self.gm.CONFIG.update(over)

    def client(self, api_key: str = KEY_ORG_A, *, max_retries: int = 0, **kw):
        hc = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_header_spy(self.app, self.seen_headers)),
            base_url="http://testserver")
        return openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=api_key,
                                  http_client=hc, max_retries=max_retries, **kw)


@pytest_asyncio.fixture()
async def hz(monkeypatch):
    """Redis-backed gateway (kill-switch/model-state read an EMPTY fakeredis, so they
    never block) with an ORG-SCOPED key plus a full adversarial key set."""
    import fakeredis.aioredis
    from ai_mesh_gateway import main as gateway_main

    state_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=state_redis)

    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    seeds = {
        KEY_ORG_A: _payload(),
        KEY_INACTIVE: _payload(is_active=False),
        KEY_EXPIRED: _payload(expires_at=expired),
        KEY_CHATONLY: _payload(
            permissions={"allowed_actions": ["chat"], "denied_actions": ["embedding"]}),
    }
    for raw, pl in seeds.items():
        await auth_redis.set(f"auth:apikey:{_hash(raw)}", json.dumps(pl))
    await auth_redis.set(f"auth:apikey:{_hash(KEY_CORRUPT)}", "{not-json")  # -> 500 branch

    h = Harness(app, auth_redis, gateway_main)

    async def _cap_chat(body, redacted_prompt=None, **kw):
        h.chat_bodies.append(copy.deepcopy(body))
        return await T._fake_completion(body, redacted_prompt, **kw)

    async def _cap_embed(body, *a, **kw):
        h.embed_bodies.append(copy.deepcopy(body))
        return await T._fake_embedding(body, *a, **kw)

    gateway_main.LLM_ROUTER.acompletion = AsyncMock(side_effect=_cap_chat)
    gateway_main.LLM_ROUTER.aembedding = AsyncMock(side_effect=_cap_embed)
    h.set_org_config()
    yield h
    await state_redis.aclose()
    await auth_redis.aclose()


async def _chat(hz, *, key=KEY_ORG_A, retries=0, raw=False, ckw=None, **body):
    body.setdefault("model", "gpt-4o-mini")
    body.setdefault("messages", [{"role": "user", "content": "hello"}])
    c = hz.client(key, max_retries=retries, **(ckw or {}))
    try:
        api = c.chat.completions.with_raw_response if raw else c.chat.completions
        return await api.create(**body)
    finally:
        await c.close()


async def _embed(hz, *, key=KEY_ORG_A, **body):
    body.setdefault("model", "zs-embed")
    body.setdefault("input", "hello")
    c = hz.client(key)
    try:
        return await c.embeddings.create(**body)
    finally:
        await c.close()


async def _call(hz, surface, **kw):  # dispatch one call at either OpenAI surface
    return await (_chat(hz, **kw) if surface == "chat" else _embed(hz, **kw))

# A — AUTHENTICATION: exception TYPE + status, on chat AND embeddings

@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["chat", "embeddings"])
@pytest.mark.parametrize("key,exc,status", [
    (KEY_SHORT, openai.AuthenticationError, 401),        # malformed (too short)
    (KEY_WRONGPFX, openai.AuthenticationError, 401),     # wrong prefix / foreign
    (KEY_UNKNOWN, openai.AuthenticationError, 401),      # never provisioned
    (KEY_INACTIVE, openai.PermissionDeniedError, 403),   # revoked / disabled
    (KEY_EXPIRED, openai.PermissionDeniedError, 403),    # expired
    (KEY_CORRUPT, openai.InternalServerError, 500),      # corrupt redis payload
])
async def test_a1_bad_credentials_reject_on_both_surfaces(hz, surface, key, exc, status):
    """Every non-usable credential fails CLOSED on BOTH surfaces, never reaching
    the upstream model."""
    with pytest.raises(exc) as ei:
        await _call(hz, surface, key=key)
    assert ei.value.status_code == status
    assert hz.chat_bodies == [] and hz.embed_bodies == [], "upstream reached without auth"


@pytest.mark.asyncio
async def test_a2_missing_authorization_header_is_401(hz):
    """No Authorization header -> 401 with the RFC-9728 WWW-Authenticate challenge."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hz.app),
                                 base_url="http://testserver") as raw:
        r = await raw.post("/v1/chat/completions",
                           json={"model": "gpt-4o-mini", "messages": []})
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, KEY_UNKNOWN, KEY_INACTIVE, KEY_EXPIRED])
async def test_a3_soft_auth_models_path_is_re_gated_in_the_handler(hz, key):
    """SOFT-AUTH TRAP: /v1/models is in SOFT_AUTH_PATHS (middleware.py:51-53), so
    AuthMiddleware SWALLOWS the 401/403 and passes through with auth_context=None.
    Without a handler re-gate a revoked/expired key would enumerate the org
    catalogue. It does re-gate (main.py:13961) — all must still get 401."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=hz.app),
                                 base_url="http://testserver") as raw:
        r = await raw.get("/v1/models", headers=headers)
    assert r.status_code == 401, f"catalogue leaked to credential {key!r}: {r.text[:200]}"


@pytest.mark.asyncio
async def test_a4_models_catalogue_is_served_to_a_valid_key(hz):
    """Control for A3: the re-gate is not simply denying everyone."""
    c = hz.client()
    try:
        models = await c.models.list()
    finally:
        await c.close()
    assert any(m.id == "gpt-4o-mini" for m in models.data)


# B — TENANT ISOLATION: can a client header override the server-derived org?

@pytest.mark.asyncio
async def test_b1_org_is_derived_from_key_not_from_spoofed_headers(hz):
    """CRITICAL isolation probe: a key bound to ORG_A sends every plausible
    org-override header naming ORG_B. The org config is resolved for, and the org
    stamped on the upstream body, must both stay ORG_A."""
    await _chat(hz, ckw={"default_headers": dict(_SPOOF)})

    delivered = hz.seen_headers[-1]
    for hname, hval in _SPOOF.items():  # prove the spoof was actually sent
        assert delivered.get(hname.lower()) == hval, f"{hname} never reached the app"

    assert hz.orgs_resolved() == {ORG_A}, f"org resolution honoured a header: {hz.orgs_resolved()}"
    assert hz.chat_bodies[0]["_zs_org_slug"] == ORG_A, "upstream org binding was spoofable"


@pytest.mark.asyncio
async def test_b2_sdk_organization_kwarg_does_not_repoint_the_tenant(hz):
    """The SDK's ``organization=`` kwarg emits ``OpenAI-Organization``; it must be
    inert — tenancy comes from the key alone."""
    await _chat(hz, ckw={"organization": ORG_B})
    assert hz.seen_headers[-1].get("openai-organization") == ORG_B, "kwarg not delivered"
    assert hz.chat_bodies[0]["_zs_org_slug"] == ORG_A
    assert hz.orgs_resolved() == {ORG_A}


@pytest.mark.asyncio
async def test_b3_legacy_user_header_cannot_override_key_identity(hz):
    """proxy_chat accepts legacy X-User-ID/X-Endpoint-ID headers only on the
    auth_ctx-less branch; with a real key they must be ignored."""
    r = await _chat(hz, ckw={"default_headers": {"X-User-ID": "999999", "X-Endpoint-ID": "42"}})
    assert hz.seen_headers[-1].get("x-user-id") == "999999", "header not delivered"
    assert "999999" not in json.dumps(getattr(r, "zeroshield", None) or {})
    assert "999999" not in json.dumps(hz.chat_bodies[0]), "legacy identity reached upstream"
    assert hz.orgs_resolved() == {ORG_A}


@pytest.mark.asyncio
async def test_b4_spoof_headers_are_not_forwarded_upstream(hz):
    """Defence in depth: spoofed tenant/role headers must not be relayed to the
    provider inside the request body either."""
    await _chat(hz, ckw={"default_headers": dict(_SPOOF)})
    assert ORG_B not in json.dumps(hz.chat_bodies[0]).lower(), hz.chat_bodies[0]


# C — KEY-SCOPED MODEL ALLOWLIST

@pytest.mark.asyncio
async def test_c1_chat_model_outside_key_allowlist_is_403_not_served(hz):
    with pytest.raises(openai.PermissionDeniedError) as ei:
        await _chat(hz, model="gpt-4-turbo")
    assert ei.value.status_code == 403 and ei.value.code == "model_not_allowed"
    assert hz.chat_bodies == [], "disallowed model was silently served upstream"


@pytest.mark.asyncio
async def test_c2_embedding_model_outside_key_allowlist_is_403_not_served(hz):
    with pytest.raises(openai.PermissionDeniedError) as ei:
        await _embed(hz, model="text-embedding-3-large")
    assert ei.value.status_code == 403 and ei.value.code == "model_not_allowed"
    assert hz.embed_bodies == [], "disallowed embedding model was silently served"


@pytest.mark.asyncio
async def test_c3_allowlisted_model_is_served(hz):
    """Control: the allowlist is not simply denying everything."""
    r = await _chat(hz)
    assert r.choices[0].message.content == "Hello from upstream."
    assert len(hz.chat_bodies) == 1


# D — ACTION PERMISSIONS (permissions.allowed_actions / denied_actions)

@pytest.mark.asyncio
async def test_d1_chat_only_key_must_not_reach_embeddings(hz):
    """A key granting ONLY 'chat' and DENYING 'embedding' must be refused there.

    FIXED (I-03). permissions.allowed_actions/denied_actions were parsed into
    AuthContext and synced from the control plane (which documents them as an
    enforced RBAC payload) but read by NOTHING in the gateway. Now enforced
    centrally in AuthMiddleware via resolve_request_action/action_permitted.
    """
    with pytest.raises(openai.PermissionDeniedError):
        await _embed(hz, key=KEY_CHATONLY)


@pytest.mark.asyncio
async def test_d1_denied_action_never_reaches_the_provider(hz):
    """The denial must happen BEFORE the upstream call — otherwise the request is
    refused to the caller while still burning real BYOK provider capacity.

    (Was ``test_d1_evidence_chat_only_key_actually_gets_embeddings``, which pinned
    the DEFECT: it asserted the denied action succeeded and reached the provider.
    Inverted here now that I-03 is fixed.)
    """
    with pytest.raises(openai.PermissionDeniedError):
        await _embed(hz, key=KEY_CHATONLY)
    assert hz.embed_bodies == [], "denied action still reached the provider"


@pytest.mark.asyncio
async def test_d2_allowed_action_still_works_and_empty_permissions_are_unrestricted(hz):
    """The allowlist must not over-deny: the key's GRANTED action still works, and a
    key whose permissions were never configured stays unrestricted (mirrors
    allowed_models, where EMPTY means unrestricted) so the control cannot break
    existing keys on upgrade."""
    r = await _chat(hz, key=KEY_CHATONLY)          # 'chat' is granted
    assert r.choices[0].message.content
    r2 = await _embed(hz, key=KEY_ORG_A)           # default payload, no action limits
    assert len(r2.data) == 1


# E — TOKEN BUDGETS (assert on what UPSTREAM received, not the response)

@pytest.mark.asyncio
async def test_e1_oversized_max_tokens_is_clamped_before_upstream(hz):
    """max_response_tokens is a cost/DoS control: an absurd budget must be CLAMPED
    on the way to the provider, not merely echoed back."""
    hz.set_org_config(max_response_tokens=256)
    await _chat(hz, max_tokens=1_000_000)
    assert hz.chat_bodies[0]["max_tokens"] == 256, hz.chat_bodies[0]["max_tokens"]


@pytest.mark.asyncio
async def test_e2_max_tokens_falls_back_to_a_default_ceiling_when_org_unset(hz):
    """An org with NO configured max_response_tokens must not become unbounded: the
    4096 fallback clamp still binds it. Uses the largest value the boundary
    validator accepts, so this measures the CLAMP, not the validator."""
    from ai_mesh_gateway import main as gm
    hz.set_org_config(max_response_tokens=0)
    await _chat(hz, max_tokens=gm.MAX_OUTPUT_TOKENS_CEILING)
    sent = hz.chat_bodies[0]["max_tokens"]
    assert sent <= 65536, f"unbounded completion budget reached upstream: {sent}"
    assert sent == 4096, sent


@pytest.mark.asyncio
async def test_e2b_max_tokens_above_request_ceiling_is_400(hz):
    """Beyond MAX_OUTPUT_TOKENS_CEILING the request is refused, not silently clamped."""
    from ai_mesh_gateway import main as gm
    with pytest.raises(openai.BadRequestError) as ei:
        await _chat(hz, max_tokens=gm.MAX_OUTPUT_TOKENS_CEILING + 1)
    assert ei.value.code == "invalid_max_tokens" and ei.value.param == "max_tokens"
    assert hz.chat_bodies == []


@pytest.mark.asyncio
async def test_e3_under_budget_max_tokens_passes_through_unchanged(hz):
    """Control: the clamp must not mangle a legitimate budget."""
    hz.set_org_config(max_response_tokens=4096)
    await _chat(hz, max_tokens=64)
    assert hz.chat_bodies[0]["max_tokens"] == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["batch", "chars"])
async def test_e4_oversized_embedding_payload_is_rejected_413(hz, kind):
    """Ingress payload ceilings: an oversized embedding request is refused before
    dispatch (batch-item count and total character count)."""
    from ai_mesh_gateway import main as gm
    payload = (["x"] * (gm.MAX_EMBED_BATCH + 1) if kind == "batch"
               else "a" * (gm.MAX_EMBED_INPUT_CHARS + 1))
    with pytest.raises(openai.APIStatusError) as ei:
        await _embed(hz, input=payload)
    assert ei.value.status_code == 413
    assert hz.embed_bodies == []


# F — RATE LIMITING (DDoS-style AI traffic shaping)

class _DenyingLimiter:
    """RATE_LIMITER stand-in reporting the per-key TPM window exhausted."""
    def __init__(self):
        self.key_calls = 0

    async def check_rate_limit(self, key_hash, limit, est):
        self.key_calls += 1
        return False, limit + est

    async def check_org_rate_limit(self, org, limit, est):
        return True, 0

    async def check_model_rate_limit(self, *a, **kw):
        return True, 0


@pytest.mark.asyncio
async def test_f1_per_key_tpm_exhaustion_raises_ratelimiterror(hz, monkeypatch):
    """Per-key TPM ceiling -> SDK raises RateLimitError (429); never reaches upstream."""
    monkeypatch.setattr(hz.gm, "RATE_LIMITER", _DenyingLimiter())
    with pytest.raises(openai.RateLimitError) as ei:
        await _chat(hz)
    assert ei.value.status_code == 429 and ei.value.code == "rate_limit_exceeded"
    assert hz.chat_bodies == [], "rate-limited request still burned provider capacity"


@pytest.mark.asyncio
async def test_f2_429_carries_parseable_retry_after_via_raw_response(hz, monkeypatch):
    """.with_raw_response must expose a Retry-After the SDK's backoff can parse."""
    monkeypatch.setattr(hz.gm, "RATE_LIMITER", _DenyingLimiter())
    with pytest.raises(openai.RateLimitError) as ei:
        await _chat(hz, raw=True)
    ra = ei.value.response.headers.get("retry-after")
    assert ra is not None and int(ra) > 0, f"unparseable Retry-After: {ra!r}"
    assert ei.value.response.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_f3_ratelimit_headers_are_advertised(hz):
    raw = await _chat(hz, raw=True)
    hdrs = {k.lower() for k in raw.headers}
    assert {"x-ratelimit-limit-requests", "x-ratelimit-remaining-requests"} <= hdrs


@pytest.mark.asyncio
async def test_f4_burst_limit_shapes_a_request_flood(hz):
    """Real Redis-backed burst window: a flood inside one second must start
    returning 429 once burst_limit is passed."""
    hz.set_org_config(rate_limit_enabled=True, burst_limit=2, requests_per_minute=100000)
    statuses = []
    for _ in range(6):
        try:
            await _chat(hz)
            statuses.append(200)
        except openai.RateLimitError as e:
            statuses.append(e.status_code)
    assert 429 in statuses, f"burst flood never shaped: {statuses}"
    assert len(hz.chat_bodies) < 6, "every flood request reached the provider"


@pytest.mark.asyncio
async def test_f5_sdk_autoretry_does_not_launder_a_429_into_success(hz):
    """The SDK retries 429 by default. A persistent limit must still surface as
    RateLimitError — never be laundered into a 200."""
    hz.set_org_config(rate_limit_enabled=True, burst_limit=0, requests_per_minute=100000)
    with pytest.raises(openai.RateLimitError) as ei:
        await _chat(hz, retries=2)
    assert ei.value.status_code == 429
    assert hz.chat_bodies == []


@pytest.mark.asyncio
async def test_f6_max_retries_zero_surfaces_the_429_immediately(hz):
    hz.set_org_config(rate_limit_enabled=True, burst_limit=0, requests_per_minute=100000)
    with pytest.raises(openai.RateLimitError) as ei:
        await _chat(hz, retries=0)
    assert ei.value.status_code == 429
    assert ei.value.response.headers.get("retry-after")


@pytest.mark.asyncio
async def test_f7_per_key_tpm_also_guards_embeddings(hz, monkeypatch):
    """FIXED (I-06). /v1/embeddings enforced the per-ORG ceiling but never called
    RATE_LIMITER.check_rate_limit, so the per-KEY TPM ceiling guarding chat was
    absent and traffic could be shifted to embeddings to evade a spent budget. The
    gap was bidirectional — record_usage was missing too, so the bucket was never
    even charged. Both halves are now wired; see
    test_fix_ingress_control_parity.py for the structural guard."""
    monkeypatch.setattr(hz.gm, "RATE_LIMITER", _DenyingLimiter())
    with pytest.raises(openai.RateLimitError):
        await _embed(hz)


@pytest.mark.asyncio
async def test_f7_exhausted_key_budget_blocks_embeddings_too(hz, monkeypatch):
    """FIXED (I-06). With the per-key limiter denying every check, the embedding must
    now be REFUSED and must never reach the provider.

    (Was ``test_f7_evidence_embeddings_ignore_exhausted_key_budget``, which pinned the
    DEFECT: it asserted the limiter was never consulted and the request was served.
    Inverted here — the point of the ceiling is that an exhausted key cannot shift
    its spend to the embeddings surface.)
    """
    limiter = _DenyingLimiter()
    monkeypatch.setattr(hz.gm, "RATE_LIMITER", limiter)
    with pytest.raises(openai.RateLimitError) as ei:
        await _embed(hz)
    assert ei.value.status_code == 429
    assert limiter.key_calls >= 1, "per-key TPM ceiling was not consulted"
    assert hz.embed_bodies == [], "rate-limited embedding still reached the provider"


@pytest.mark.asyncio
async def test_f8_rate_limit_disabled_config_is_honoured(hz):
    """Control: with shaping off the same flood is fully served — proving F4
    measured the limiter, not an unrelated failure."""
    hz.set_org_config(rate_limit_enabled=False, burst_limit=1)
    for _ in range(4):
        await _chat(hz)
    assert len(hz.chat_bodies) == 4
