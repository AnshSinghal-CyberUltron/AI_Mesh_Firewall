"""Regression locks for the two CRITICAL fixes I-01 and I-02.

I-01 — raw PII/credentials shipped in ``tool_calls.function.arguments``.
    The output guard SCANS content + reasoning_content + tool_calls
    (``_extract_scannable_output_text``), but the delivered-text test fed to
    ``coalesce_output_guard_verdict_for_delivery`` was
    ``_extract_response_from_completion`` — **content only**. A match living only in
    a tool/reasoning channel therefore failed
    ``output_verdict_applies_to_delivered_text`` and the verdict was discarded as a
    false positive: no redaction, no secondary-channel neutralization (the redact
    branch never called ``_set_completion_response_text``, so
    ``_neutralize_secondary_output_channels`` never fired) and NO incident telemetry.
    A SILENT leak. Fixed by ``_client_delivered_output_text``, which enumerates the
    channels the client actually receives, at BOTH coalescer call sites.

I-02 — an UNREGISTERED gateway skipped the entire governance block.
    ``if not AGENT_ID or not CONFIG["backend_url"]`` short-circuited straight to
    ``LLM_ROUTER.acompletion()``, jumping dynamic routing, the compliance filter, the
    data-sensitivity floor, the per-key model allowlist re-check, per-model rate
    limits, the circuit breaker AND the provider-topology scrub. ``AGENT_ID`` is set
    only by control-plane registration, but ``ConfigSync`` loads the model catalogue
    from Redis INDEPENDENTLY — an unregistered worker holding a warm catalogue is a
    real production state, and in it governance was off while traffic flowed 200 OK.
    Fixed by gating the short-circuit on ROUTING-CATALOGUE availability (mirroring
    the ``_policy_cache_ready or AGENT_ID`` precedent already shipped for policy).

The two interact: I-02 routes more traffic down the connected branch, which is
exactly where the I-01 defect lives, so they are locked together here.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_fix_i01_i02_criticals.py -q -p no:cacheprovider
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

from ai_mesh_gateway import main as gm
from ai_mesh_gateway import middleware as gw_middleware
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

# Canaries: must never appear anywhere in a response envelope.
SSN = "412-55-9083"
AKIA = "AKIAIOSFODNN7EXAMPLE"
EMAIL = "victim.person@example.com"


# ═══════════════════════════════════════════════════════════════════════════
# harness
# ═══════════════════════════════════════════════════════════════════════════
def _completion(content, *, tool_args=None, reasoning=None, finish="stop") -> dict:
    """An OpenAI-shaped completion with optional secondary channels populated."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_args is not None:
        msg["tool_calls"] = [{
            "id": "call_i01", "type": "function",
            "function": {"name": "file_report", "arguments": tool_args},
        }]
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-i01", "object": "chat.completion", "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


async def _guard_app(monkeypatch, *, guard_cfg=None):
    """T._make_sdk_app with §1.7 actually wired on (T's harness sets OUTPUT_GUARD=None).

    ``org_slug`` is "" in T._auth_payload(), so proxy_chat passes org_config=None into
    OutputGuard.inspect and the guard reads its per-detector actions from the config
    dict handed to its constructor — the dict built here.
    """
    from ai_mesh_gateway.output_guard import OutputGuard

    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cfg = dict(T.TEST_CONFIG)
    cfg["output_guard_enabled"] = True
    cfg.update(guard_cfg or {})
    org_config = dict(T.TEST_CONFIG)
    org_config["output_scan_enabled"] = True
    gm.CONFIG_SYNC.get_config = MagicMock(return_value=org_config)
    monkeypatch.setattr(gm, "CONFIG", cfg)
    monkeypatch.setattr(gm, "OUTPUT_GUARD", OutputGuard(gm.INPUT_SCANNER, cfg))
    return app, auth_redis


def _set_upstream(completion):
    async def _fake(body, redacted_prompt=None, **_kw):
        return 200, completion
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=_fake)


async def _raw_post(app, payload):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                 headers={"Authorization": f"Bearer {T.API_KEY}"}) as c:
        return await c.post("/v1/chat/completions", json=payload)


def _canaries_in(text: str) -> list[str]:
    return [c for c in (SSN, AKIA, EMAIL) if c in text]


# ═══════════════════════════════════════════════════════════════════════════
# I-01 — the delivered-envelope helper itself
# ═══════════════════════════════════════════════════════════════════════════
def test_i01_helper_folds_every_client_delivered_channel():
    """The helper must see each channel the client actually receives."""
    comp = _completion("visible answer",
                       tool_args=json.dumps({"ssn": SSN}), reasoning=f"thinking {AKIA}")
    comp["choices"][0]["message"]["refusal"] = f"refused: {EMAIL}"
    delivered = gm._client_delivered_output_text(comp)
    assert "visible answer" in delivered
    assert SSN in delivered, "tool_calls.function.arguments not folded"
    assert AKIA in delivered, "reasoning_content not folded"
    assert EMAIL in delivered, "refusal not folded"
    assert "file_report" in delivered, "tool_calls.function.name not folded"


def test_i01_helper_is_not_an_alias_of_the_scan_text():
    """The duplication is DELIBERATE: the scan input may grow sources the client never
    sees, and the coalescer's FP suppression depends on the delivered list staying
    delivered-only. Aliasing would make the coalescer permanently a no-op."""
    assert gm._client_delivered_output_text is not gm._extract_scannable_output_text
    # An empty / malformed envelope must degrade to "" rather than raise.
    assert gm._client_delivered_output_text({}) == ""
    assert gm._client_delivered_output_text({"choices": []}) == ""
    assert gm._client_delivered_output_text({"choices": [None, "x"]}) == ""


def test_i01_coalescer_still_suppresses_a_genuine_undelivered_match():
    """CRITICAL CONSTRAINT: widening delivered_text must NOT turn the coalescer into a
    no-op. A match that exists only in bytes the client never sees (a derived/decoded
    form, RAG/classifier metadata) must still be downgraded to allow."""
    from ai_mesh_gateway.output_guard import (
        OutputVerdict, coalesce_output_guard_verdict_for_delivery,
    )
    comp = _completion("a wholly benign answer with nothing sensitive in it")
    delivered = gm._client_delivered_output_text(comp)
    assert _canaries_in(delivered) == []

    undelivered = OutputVerdict(action="redact", threat_type="pii",
                                matched_values={"ssn": SSN})
    coalesced = coalesce_output_guard_verdict_for_delivery(
        undelivered, delivered_text=delivered)
    assert coalesced.action == "allow", (
        f"FP suppression lost — undelivered match produced action={coalesced.action}")

    # …but the SAME verdict against an envelope that DOES carry the value survives.
    leaky = gm._client_delivered_output_text(
        _completion("clean answer", tool_args=json.dumps({"ssn": SSN})))
    survived = coalesce_output_guard_verdict_for_delivery(
        OutputVerdict(action="redact", threat_type="pii", matched_values={"ssn": SSN}),
        delivered_text=leaky)
    assert survived.action == "redact", (
        "tool-channel match was still discarded as a false positive (I-01 defect)")


# ═══════════════════════════════════════════════════════════════════════════
# I-01 — end to end through the real app
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_i01_tool_call_arguments_do_not_leak_when_content_is_clean(monkeypatch):
    """THE DEFECT. PII/credentials ONLY in tool_calls.function.arguments, clean content.

    Pre-fix: 200 OK with the raw SSN + AWS key in the tool arguments, silently.
    Post-fix: the verdict survives coalescing. Masking clean content is a no-op, so
    there is no partial delivery to make and enforcement fails CLOSED with a 400.
    Either way the raw values must not appear ANYWHERE in the response.
    """
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    try:
        _set_upstream(_completion(
            "Calling the reporting tool now.",
            tool_args=json.dumps({"ssn": SSN, "key": AKIA}), finish="tool_calls"))
        r = await _raw_post(app, {"model": "gpt-4o-mini",
                                  "messages": [{"role": "user", "content": "send"}]})
        assert _canaries_in(r.text) == [], _canaries_in(r.text)
        assert r.status_code == 400, r.text
        body = r.json()
        # The leak is no longer SILENT: the operator gets a real incident.
        assert body.get("code") == "output_blocked", body
        assert body.get("category") == "pii", body
        assert body.get("blocked_by") == "output_guardrail", body
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_i01_reasoning_content_does_not_leak_when_content_is_clean(monkeypatch):
    """Same defect via the reasoning channel — also returned to the client."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    try:
        _set_upstream(_completion(
            "Here is the summary you asked for.",
            reasoning=f"The user's SSN is {SSN} and their email is {EMAIL}."))
        r = await _raw_post(app, {"model": "gpt-4o-mini",
                                  "messages": [{"role": "user", "content": "summarise"}]})
        assert _canaries_in(r.text) == [], _canaries_in(r.text)
        assert r.status_code == 400, r.text
        assert r.json().get("code") == "output_blocked", r.text
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_i01_control_mixed_case_redacts_and_neutralizes_the_tool_channel(monkeypatch):
    """CONTROL: with the PII ALSO in content, masking content is a real change, so the
    redact branch runs _set_completion_response_text -> _neutralize_secondary_output_
    channels blanks the tool args and the answer is DELIVERED (200), not blocked."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    try:
        _set_upstream(_completion(
            f"The SSN on file is {SSN}; filing it now.",
            tool_args=json.dumps({"ssn": SSN, "key": AKIA}), finish="tool_calls"))
        r = await _raw_post(app, {"model": "gpt-4o-mini",
                                  "messages": [{"role": "user", "content": "file it"}]})
        assert r.status_code == 200, r.text
        msg = r.json()["choices"][0]["message"]
        assert SSN not in (msg.get("content") or "")
        assert msg["tool_calls"][0]["function"]["arguments"] == "", msg["tool_calls"]
        assert _canaries_in(r.text) == [], _canaries_in(r.text)
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_i01_benign_tool_call_still_ships_untouched(monkeypatch):
    """The fix must not break agentic tool calling: benign arguments still deliver."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    try:
        _set_upstream(_completion(
            "Looking up the weather.",
            tool_args=json.dumps({"city": "Paris", "unit": "celsius"}),
            finish="tool_calls"))
        r = await _raw_post(app, {"model": "gpt-4o-mini",
                                  "messages": [{"role": "user", "content": "weather?"}]})
        assert r.status_code == 200, r.text
        fn = r.json()["choices"][0]["message"]["tool_calls"][0]["function"]
        assert json.loads(fn["arguments"]) == {"city": "Paris", "unit": "celsius"}
        assert fn["name"] == "file_report"
    finally:
        await auth_redis.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# I-02 — governance must not depend on control-plane registration
# ═══════════════════════════════════════════════════════════════════════════
ORG = "org-i02"


def _cat(name, mid, provider, sens, tags, *, risk=0.2, cost=0.15, sla=800, prio=5):
    return {"model_name": name, "model_id": mid, "provider": provider,
            "is_active": True, "api_key_set": True, "risk_score": risk,
            "cost_per_1k_input_tokens": cost, "latency_sla_ms": sla,
            "routing_priority": prio, "data_sensitivity_level": sens,
            "compliance_tags": tags}


CATALOGUE = [
    _cat("gpt-4o-mini", "gpt-4o-mini", "openai", "public", []),
    _cat("claude-3-5-sonnet", "anthropic/claude-3-5-sonnet", "anthropic",
         "restricted", ["hipaa", "gdpr"], risk=0.05, cost=3.0, sla=4000, prio=9),
]
ALL_NAMES = [m["model_name"] for m in CATALOGUE]


class _Upstream:
    """Records exactly what the gateway asked the upstream to serve."""

    def __init__(self):
        self.bodies: list[dict] = []

    async def acompletion(self, body, redacted_prompt=None, **_kw):
        self.bodies.append(json.loads(json.dumps(body, default=str)))
        return 200, _completion("ok")

    @property
    def served_model(self) -> str:
        assert self.bodies, "upstream was never called"
        return self.bodies[-1]["model"]


async def _gov_build(monkeypatch, *, catalogue=CATALOGUE, allowed_models=None):
    """An UNREGISTERED gateway (AGENT_ID=None, backend_url="") whose ConfigSync holds
    a warm routing catalogue — the exact production state I-02 was about."""
    from ai_mesh_gateway.llm_router import LLMRouter
    from ai_mesh_gateway.scanner import InputScanner

    names = [m["model_name"] for m in catalogue]
    payload = dict(T._auth_payload(), org_slug=ORG, organization_id=ORG,
                   allowed_models=list(ALL_NAMES if allowed_models is None else allowed_models))
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await auth_redis.set(
        f"auth:apikey:{hashlib.sha256(T.API_KEY.encode('utf-8')).hexdigest()}",
        json.dumps(payload))

    async def _get_redis(self):
        return auth_redis
    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)

    cfg = dict(T.TEST_CONFIG)
    cfg["routing_enabled"] = True
    cfg["output_policy_enabled"] = False  # separate capability; would call the CP
    cfg["backend_url"] = ""               # UNREGISTERED

    cs = MagicMock()
    cs.get_config = MagicMock(return_value=dict(cfg))
    cs.get_model_routing = MagicMock(return_value=[dict(m) for m in catalogue])
    cs.reload_models_now = AsyncMock()
    cs.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})

    # Real LLMRouter scoring arithmetic without litellm/network init.
    real = LLMRouter.__new__(LLMRouter)
    real._config = {"litellm_default_model": ""}
    real._active_model_names = list(names)
    real._qualified_model_names = set(names)

    up = _Upstream()
    lr = MagicMock()
    lr.acompletion = AsyncMock(side_effect=up.acompletion)
    lr.acompletion_stream = T._fake_stream
    lr.aembedding = AsyncMock(side_effect=T._fake_embedding)
    lr.get_model_list = MagicMock(return_value=[
        {"id": n, "object": "model", "created": 1704067200, "owned_by": "openai"}
        for n in names])
    lr.estimate_prompt_tokens = MagicMock(return_value=500)
    lr.adjudicate_model_selection = real.adjudicate_model_selection
    lr.resolve_runtime_selection = real.resolve_runtime_selection

    for attr, val in {
        "CONFIG": dict(cfg), "CONFIG_SYNC": cs, "LLM_ROUTER": lr,
        "INPUT_SCANNER": InputScanner(thread_pool_size=2),
        "AGENT_ID": None,                       # <- NEVER registered
        "_emit_telemetry": lambda **_k: None,
        "_audit_fire_and_forget": lambda **_k: None,
        "_policy_check_cached": lambda *a, **k: (200, {}),
        **dict.fromkeys(("POLICY_SYNC", "RATE_LIMITER", "CIRCUIT_BREAKER",
                         "REDIS_CLIENT", "TELEMETRY", "OUTPUT_GUARD")),
    }.items():
        monkeypatch.setattr(gm, attr, val)
    monkeypatch.setenv("ROUTING_ADJUDICATOR_ALWAYS", "false")

    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY, max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=gm.app),
                                      base_url="http://testserver"))
    return client, up, auth_redis


@pytest_asyncio.fixture()
async def gov(monkeypatch):
    made = []

    async def _factory(**kw):
        client, up, auth_redis = await _gov_build(monkeypatch, **kw)
        made.append((client, auth_redis))
        return client, up

    yield _factory
    for client, auth_redis in made:
        await client.close()
        await auth_redis.aclose()


async def _chat(client, model="auto", prefs=None):
    return await client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "quarterly plan"}],
        extra_body={"routing_preferences": prefs} if prefs else None)


@pytest.mark.asyncio
async def test_i02_unregistered_gateway_enforces_data_sensitivity_floor(gov):
    """restricted data must NOT be served on a public-tier model just because this
    worker never registered an AGENT_ID."""
    client, up = await gov()
    await _chat(client, model="gpt-4o-mini", prefs={"data_sensitivity": "restricted"})
    assert up.served_model == "claude-3-5-sonnet", (
        f"unregistered gateway served restricted data on {up.served_model}")


@pytest.mark.asyncio
async def test_i02_unregistered_gateway_enforces_compliance_filter(gov):
    """An UNSATISFIABLE compliance requirement must be refused, not quietly served."""
    client, up = await gov()
    with pytest.raises(openai.PermissionDeniedError):
        await _chat(client, prefs={"compliance_requirements": ["pci-dss"]})
    assert up.bodies == [], "compliance-blocked request still reached the upstream"


@pytest.mark.asyncio
async def test_i02_unregistered_gateway_enforces_key_model_allowlist(gov):
    """A model OUTSIDE the caller's key allowlist must never reach the wire.

    Pre-fix the unregistered branch forwarded ``body["model"]`` straight upstream, so
    an explicit request for a model the key does not own was served verbatim. The
    allowlist filter now runs, so claude is excluded from the candidate set and can
    never be the served model. (Requesting it does not 403 here because routing is
    active and re-selects an entitled model — enforcement by exclusion, not refusal.)
    """
    client, up = await gov(allowed_models=["gpt-4o-mini"])
    await _chat(client, model="claude-3-5-sonnet")
    assert up.served_model == "gpt-4o-mini", (
        f"key entitlement bypassed on the unregistered path: served {up.served_model}")


@pytest.mark.asyncio
async def test_i02_genuinely_standalone_deployment_still_serves(gov):
    """THE BLAST-RADIUS GUARD. With NO routing catalogue and no control plane there is
    nothing to govern against, so the fast path must still forward to the LLM — the
    fix is purely additive and must not break a genuinely standalone deployment."""
    client, up = await gov(catalogue=[])
    r = await client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hello"}])
    assert r.choices[0].message.content
    assert up.served_model == "gpt-4o-mini"


# ═══════════════════════════════════════════════════════════════════════════
# I-01b — logprobs + annotations (found by the adversarial exploit lens)
#
# The original I-01 fix folded content/reasoning/refusal/audio/tool_calls into the
# delivered text, but MISSED two further client-delivered channels:
#   * ``annotations``      — model-authored url_citation / note objects (message)
#   * ``logprobs``         — a token-by-token echo of the answer (CHOICE level)
# ``logprobs`` is the dangerous one: it is client-triggerable (send logprobs=true)
# and reconstructs the ORIGINAL answer by concatenation even after ``content`` has
# been redacted. Proven live before the fix:
#     content : 'The customer SSN is ***-**-9083.'
#     tokens  : ['The',' customer',' SSN',' is',' ','412-55-9083','.']
#     -> reconstructed 'The customer SSN is 412-55-9083.'  (HTTP 200)
# ═══════════════════════════════════════════════════════════════════════════
def _completion_with_logprobs(content, tokens, *, annotations=None):
    ch: dict = {
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "content": content},
        "logprobs": {"content": [{"token": t, "logprob": -0.1, "top_logprobs": []}
                                 for t in tokens]},
    }
    if annotations is not None:
        ch["message"]["annotations"] = annotations
    return {
        "id": "chatcmpl-i01b", "object": "chat.completion", "created": 1700000000,
        "model": "gpt-4o-mini", "choices": [ch],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_i01b_helper_reconstructs_a_token_split_secret():
    """Tokens MUST be joined with NO separator. A tokenizer splits a secret across
    several tokens, so folding them individually (or newline-joined) would never
    contain it as a substring and the match would be missed entirely."""
    comp = _completion_with_logprobs("clean answer", ["412-", "55-", "9083"])
    assert SSN in gm._client_delivered_output_text(comp), "token-split secret not rebuilt"


def test_i01b_scan_text_is_a_superset_of_delivered_text():
    """INVARIANT: a channel counted as delivered but never SCANNED is a silent leak —
    detection never fires, so there is no verdict to enforce."""
    comp = _completion_with_logprobs(
        "clean answer", ["412-", "55-", "9083"], annotations=[{"type": "note", "text": EMAIL}])
    scan = gm._extract_scannable_output_text(comp)
    assert SSN in scan, "logprobs not scanned"
    assert EMAIL in scan, "annotations not scanned"


@pytest.mark.asyncio
async def test_i01b_logprobs_and_annotations_are_neutralized_on_enforcement(monkeypatch):
    """The client-triggerable bypass: redacted content shipped beside raw tokens."""
    app, _ = await _guard_app(monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion_with_logprobs(
        f"The customer SSN is {SSN}.",
        ["The", " customer", " SSN", " is", " ", SSN, "."],
        annotations=[{"type": "note", "text": f"ssn={SSN}"}]))
    r = await _raw_post(app, {"model": "gpt-4o-mini", "logprobs": True,
                              "messages": [{"role": "user", "content": "hi"}]})
    assert SSN not in r.text, "raw secret still reachable via logprobs/annotations"
    j = r.json()
    ch = j["choices"][0]
    assert "***" in (ch["message"]["content"] or ""), "content was not redacted"
    assert not (ch.get("logprobs") or {}).get("content") if ch.get("logprobs") else True
    assert not ch["message"].get("annotations")


@pytest.mark.asyncio
async def test_i01b_secret_only_in_side_channels_is_not_delivered(monkeypatch):
    """Content is CLEAN and the secret exists only in logprobs/annotations. Pre-fix the
    scanner never saw those channels, so no verdict fired and the secret shipped raw."""
    app, _ = await _guard_app(monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion_with_logprobs(
        "Here is the summary you asked for.", ["Here", " is", " ", "412-", "55-", "9083"],
        annotations=[{"type": "url_citation", "text": f"ref {SSN}"}]))
    r = await _raw_post(app, {"model": "gpt-4o-mini", "logprobs": True,
                              "messages": [{"role": "user", "content": "hi"}]})
    assert SSN not in r.text, "side-channel-only secret was delivered to the client"
