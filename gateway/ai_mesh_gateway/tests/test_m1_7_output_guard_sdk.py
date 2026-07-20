"""MODULE 1.7 — Generator-Level Output Guardrails, through the STOCK openai SDK.

Every existing ``test_openai_sdk_compat*.py`` file nulls ``OUTPUT_GUARD`` (see
``test_openai_sdk_compat.py:202``), so §1.7 has never been exercised on the SDK
path. This file constructs a REAL ``OutputGuard(scanner=INPUT_SCANNER, config=CONFIG)``
exactly as ``main.py:4836`` does and drives it with the unmodified SDK.

What is proven here (all assertions are honest; xfails carry captured evidence):
  * the real action matrix (detector x configured action) as the code ACTUALLY
    behaves — several configured actions collapse;
  * a secret split across SSE chunk boundaries (down to 1 char/chunk) is still
    masked, over a real TCP socket (uvicorn), not just ASGITransport;
  * the response envelope (incl. ``pipeline_trace.output_text``) is scanned for
    the raw secret;
  * tool_call ``arguments`` — an exfil channel that IS scanned but is NOT always
    sanitized.

Run:
  cd gateway && .venv/bin/python -m pytest \
      ai_mesh_gateway/tests/test_m1_7_output_guard_sdk.py -q -p no:cacheprovider
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import fakeredis.aioredis
import httpx
import openai
import pytest
import pytest_asyncio
import uvicorn

import ai_mesh_gateway.main as gm
from ai_mesh_gateway import middleware as gw_middleware
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

# ── canaries: each is a distinct, unmistakable literal so an envelope-wide
# substring scan can attribute a leak to a specific detector. ────────────────
SSN = "412-55-9083"
AKIA = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
INTERNAL_IP = "10.42.7.19"
LONG_KEY = "sk-" + "aB3xY9zQ7m" * 30  # 303 chars: exceeds the stream buffer edge
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA" + "b7Qx" * 40 + "\n"
    "-----END RSA PRIVATE KEY-----"
)
ALL_CANARIES = {
    "SSN": SSN, "AKIA": AKIA, "AWS_SECRET": AWS_SECRET,
    "INTERNAL_IP": INTERNAL_IP, "LONG_KEY": LONG_KEY,
}


def _canaries_in(blob: str) -> list[str]:
    """Names of raw canaries appearing ANYWHERE in a serialized envelope."""
    return [name for name, value in ALL_CANARIES.items() if value in blob]


def _completion(content, *, tool_args=None, reasoning=None, finish="stop"):
    msg = {"role": "assistant", "content": content}
    if tool_args is not None:
        msg["tool_calls"] = [{
            "id": "call_og_1", "type": "function",
            "function": {"name": "send_report", "arguments": tool_args},
        }]
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-og-001", "object": "chat.completion", "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
    }


def _sse_chunk(token, finish=None):
    return "data: " + json.dumps({
        "id": "chatcmpl-og-stream", "object": "chat.completion.chunk",
        "created": 1700000000, "model": "gpt-4o-mini",
        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": finish}],
    }) + "\n\n"


def _split_stream(text: str, chunk_size: int):
    """Upstream stream stub that slices ``text`` into fixed-size deltas — the
    adversarial primitive: a secret straddling arbitrary chunk boundaries."""
    parts = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]

    async def gen(body, redacted_prompt=None, metrics=None, **_kw):
        for i, part in enumerate(parts):
            yield _sse_chunk(part, "stop" if i == len(parts) - 1 else None)
        if metrics is not None:
            metrics.completed = True
        yield "data: [DONE]\n\n"
    return gen


def _sse_content(raw_body: str) -> str:
    """Reassemble the delta content a streaming SDK caller would concatenate."""
    out = []
    for line in raw_body.splitlines():
        if not line.startswith("data: ") or "[DONE]" in line:
            continue
        try:
            frame = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        for choice in frame.get("choices") or []:
            out.append((choice.get("delta") or {}).get("content") or "")
    return "".join(out)


# ── in-process harness: the real app + a REAL OutputGuard ────────────────────
async def _guard_app(monkeypatch, *, guard_cfg=None, org_over=None):
    """T._make_sdk_app, but with §1.7 actually wired on.

    ``org_slug`` is "" in ``T._auth_payload()``, so ``proxy_chat`` passes
    ``org_config=None`` into ``OutputGuard.inspect`` and the guard reads its
    per-detector actions from the config dict handed to its constructor — which
    is the dict returned here, so tests drive the matrix through ``guard_cfg``.
    """
    from ai_mesh_gateway.output_guard import OutputGuard

    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cfg = dict(T.TEST_CONFIG)
    cfg["output_guard_enabled"] = True
    cfg.update(guard_cfg or {})
    org_config = dict(T.TEST_CONFIG)
    org_config["output_scan_enabled"] = True
    org_config.update(org_over or {})
    gm.CONFIG_SYNC.get_config = MagicMock(return_value=org_config)
    monkeypatch.setattr(gm, "CONFIG", cfg)
    monkeypatch.setattr(gm, "OUTPUT_GUARD", OutputGuard(gm.INPUT_SCANNER, cfg))
    return app, auth_redis


def _sdk(app):
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    return openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=T.API_KEY,
                              http_client=http_client, max_retries=0)


async def _raw_post(app, payload):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                 headers={"Authorization": f"Bearer {T.API_KEY}"}) as c:
        return await c.post("/v1/chat/completions", json=payload)


def _set_upstream(completion):
    async def fake(body, redacted_prompt=None, **_kw):
        return 200, completion
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=fake)


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE ACTION MATRIX — what each configured action ACTUALLY does
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action,expect_status,expect_masked,expect_raw_delivered", [
    ("block",   400, False, False),
    ("redact",  200, True,  False),
    ("rewrite", 200, False, False),   # rewrite re-infers; canary must not survive
    ("flag",    200, False, True),    # §1.7 flag == deliver + flag (by design)
    ("allow",   200, False, True),
])
async def test_pii_action_matrix(monkeypatch, action, expect_status, expect_masked,
                                 expect_raw_delivered):
    """PII (SSN) in model output, one row per configured ``output_pii_action``."""
    app, auth_redis = await _guard_app(monkeypatch, guard_cfg={"output_pii_action": action})
    calls = {"n": 0}

    async def fake(body, redacted_prompt=None, **_kw):
        calls["n"] += 1
        # 1st call = the original generation; 2nd = the guard's rewrite re-inference
        if calls["n"] == 1:
            return 200, _completion(f"Customer SSN {SSN} confirmed.")
        return 200, _completion("I cannot share that information.")
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=fake)

    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "lookup"}]})
    assert resp.status_code == expect_status, resp.text
    if expect_status == 200:
        delivered = resp.json()["choices"][0]["message"]["content"]
        assert (SSN in delivered) is expect_raw_delivered, delivered
        if expect_masked:
            assert "***-**-9083" in delivered, delivered
        if action == "rewrite":
            assert calls["n"] == 2, "rewrite must re-infer through the router"
            assert delivered == "I cannot share that information."
    else:
        # a hard block must not echo the raw canary back in the block envelope
        assert SSN not in resp.text
    await auth_redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["block", "redact", "rewrite", "flag", "allow"])
async def test_credential_action_is_ignored_and_always_collapses_to_redact(
        monkeypatch, action):
    """FINDING M17-02 — ``output_credential_action`` is a dead knob on this path.

    An AWS key pair in model output is claimed by the static PII/secret detector
    FIRST (verdict.threat_type == "pii", matched_patterns == ["aws_access_key"]),
    so ``_action("output_credential_action", ...)`` never selects the action.
    Every one of the five configured actions — including ``block`` and ``allow`` —
    produces the same 200 + masked-in-place result.

    This FAILS SAFE (the secret is always masked, ``allow`` cannot leak it), so it
    is recorded as an operator-control gap, not a data-exposure bug.
    """
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_credential_action": action})
    _set_upstream(_completion(
        f"aws_access_key_id={AKIA} aws_secret_access_key={AWS_SECRET}"))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "creds"}]})
    assert resp.status_code == 200, resp.text
    delivered = resp.json()["choices"][0]["message"]["content"]
    assert AKIA not in delivered and AWS_SECRET not in delivered, delivered
    assert "AKIA****MPLE" in delivered, delivered
    assert _canaries_in(resp.text) == [], _canaries_in(resp.text)
    await auth_redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,expect_status", [("redact", 200), ("block", 400)])
async def test_private_key_block_and_redact(monkeypatch, action, expect_status):
    """A PEM private key is honoured as block (400) or masked to [PRIVATE_KEY]."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": action,
                                "output_credential_action": action})
    _set_upstream(_completion(PRIVATE_KEY))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "key"}]})
    assert resp.status_code == expect_status, resp.text
    assert "b7Qxb7Qx" not in resp.text, "raw private-key body escaped the guard"
    if expect_status == 200:
        assert resp.json()["choices"][0]["message"]["content"] == "[PRIVATE_KEY]"
    await auth_redis.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,masked,raw_delivered", [
    ("block", None, None), ("redact", True, False),
    ("flag", False, True), ("allow", False, True),
])
async def test_ip_leakage_action_matrix(monkeypatch, action, masked, raw_delivered):
    """Internal-infrastructure (RFC1918) leakage across configured actions."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_ip_leakage_action": action,
                                "output_pii_action": "allow"})
    _set_upstream(_completion(
        f"Connect to the internal host at {INTERNAL_IP}:5432 behind the firewall."))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "where"}]})
    if action == "block":
        assert resp.status_code == 400, resp.text
        assert INTERNAL_IP not in resp.text
    else:
        assert resp.status_code == 200, resp.text
        delivered = resp.json()["choices"][0]["message"]["content"]
        assert (INTERNAL_IP in delivered) is raw_delivered, delivered
        if masked:
            assert "[INTERNAL_IPV4_REDACTED]" in delivered, delivered
    await auth_redis.aclose()


@pytest.mark.asyncio
async def test_already_masked_pii_is_not_double_redacted(monkeypatch):
    """Prior art: already-masked content has no raw value to protect, so a redact
    verdict must not mangle it further nor trip the redact-no-op fail-closed block."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    masked = "The SSN on file is ***-**-1234 (already masked)."
    _set_upstream(_completion(masked))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "ssn"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == masked
    await auth_redis.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 2. TOOL-CALL ARGUMENTS — the structured exfil channel
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tool_call_arguments_are_scanned_and_neutralized_when_content_mutates(
        monkeypatch):
    """Control case: when the guard MUTATES ``content``, the secondary channels
    (tool_calls.function.arguments) are correctly blanked by
    ``_neutralize_secondary_output_channels``. This proves the mechanism exists."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion(
        f"Here it is: {SSN}",
        tool_args=json.dumps({"ssn": SSN, "key": AKIA}), finish="tool_calls"))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "send"}]})
    assert resp.status_code == 200, resp.text
    msg = resp.json()["choices"][0]["message"]
    assert SSN not in (msg.get("content") or "")
    assert msg["tool_calls"][0]["function"]["arguments"] == ""
    assert _canaries_in(resp.text) == [], _canaries_in(resp.text)
    await auth_redis.aclose()


@pytest.mark.asyncio
async def test_tool_call_arguments_do_not_leak_raw_pii_when_content_is_clean(monkeypatch):
    """FINDING M17-01 (CRITICAL) — FIXED by I-01. A model steered to put the secret
    ONLY in the tool channel used to exfiltrate it: the guard detected the PII, but
    the coalescer tested the match against CONTENT ONLY, found nothing, and discarded
    the verdict as a false positive — so no redaction, no secondary-channel
    neutralization and NO incident telemetry (a silent leak, 200 OK with the raw SSN
    and AWS key in tool_calls.function.arguments).

    Post-fix the coalescer tests the FULL DELIVERED envelope, so the verdict survives.
    Content is clean, so masking it is a no-op and there is no partial delivery to
    make: enforcement fails CLOSED with a 400 output_blocked rather than shipping the
    tool channel. Either way the raw values must not appear anywhere in the response.
    """
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion(
        "Calling the reporting tool now.",
        tool_args=json.dumps({"ssn": SSN, "key": AKIA}), finish="tool_calls"))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "send"}]})
    try:
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body.get("code") == "output_blocked", body
        assert body.get("category") == "pii", body
        assert SSN not in resp.text, "raw SSN shipped in the response envelope"
        assert AKIA not in resp.text, "raw AWS key shipped in the response envelope"
        assert _canaries_in(resp.text) == [], _canaries_in(resp.text)
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_json_mode_structured_content_is_redacted(monkeypatch):
    """JSON-mode output is plain ``content``, so it IS covered by the redactor."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion(json.dumps({"holder": "Jane", "ssn": SSN})))
    resp = await _raw_post(app, {
        "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "json"}],
        "response_format": {"type": "json_object"}})
    assert resp.status_code == 200, resp.text
    delivered = resp.json()["choices"][0]["message"]["content"]
    assert SSN not in delivered and "***-**-9083" in delivered, delivered
    await auth_redis.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 3. ENVELOPE / TRACE LEAKAGE + SDK PARSEABILITY
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["block", "redact", "rewrite"])
async def test_no_canary_anywhere_in_envelope_or_headers(monkeypatch, action):
    """Refutes the prior-art suspicion that ``pipeline_trace.output_text`` carries
    unscrubbed content: under block/redact/rewrite the raw canary appears in NO
    part of the serialized body (zeroshield, pipeline_trace, output_text,
    final_response) and in no response header."""
    app, auth_redis = await _guard_app(monkeypatch, guard_cfg={"output_pii_action": action})
    _set_upstream(_completion(f"Customer SSN {SSN} and key {AKIA}."))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "lookup"}]})
    assert _canaries_in(resp.text) == [], f"{action}: {_canaries_in(resp.text)}"
    header_blob = json.dumps(dict(resp.headers))
    assert _canaries_in(header_blob) == [], header_blob
    if resp.status_code == 200:
        trace = resp.json().get("pipeline_trace") or {}
        assert SSN not in json.dumps(trace)
        assert SSN not in (trace.get("output_text") or "")
        assert SSN not in (trace.get("final_response") or "")
    await auth_redis.aclose()


@pytest.mark.asyncio
async def test_sdk_still_parses_completion_after_guard_mutation(monkeypatch):
    """A redacted response must remain a valid ChatCompletion for the stock SDK,
    with ``finish_reason`` and ``usage`` intact after the guard rewrote content."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion(f"Customer SSN {SSN} confirmed."))
    client = _sdk(app)
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "lookup"}])
        assert completion.choices[0].finish_reason == "stop"
        assert completion.choices[0].message.role == "assistant"
        assert SSN not in (completion.choices[0].message.content or "")
        assert completion.usage.total_tokens == 14
    finally:
        await client.close()
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_blocked_output_surfaces_as_sdk_api_status_error(monkeypatch):
    """An output-guard block is a well-formed SDK error, not a truncated 200."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "block"})
    _set_upstream(_completion(f"Customer SSN {SSN} confirmed."))
    client = _sdk(app)
    try:
        with pytest.raises(openai.APIStatusError) as exc:
            await client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "lookup"}])
        assert exc.value.status_code == 400
        assert exc.value.code == "content_filter"
        assert exc.value.request_id
        assert SSN not in json.dumps(exc.value.body or {})
    finally:
        await client.close()
        await auth_redis.aclose()


# FINDING M17-03 FIXED (I-02): the unregistered path now returns through the connected
# branch, whose envelope reports the OUTPUT delivery action, so an SDK caller can tell a
# mutated answer from an untouched one programmatically. xfail removed — this passes.
@pytest.mark.asyncio
async def test_envelope_reports_the_action_that_actually_ran(monkeypatch):
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "redact"})
    _set_upstream(_completion(f"Customer SSN {SSN} confirmed."))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "lookup"}]})
    try:
        body = resp.json()
        assert "***-**-9083" in body["choices"][0]["message"]["content"]  # redact ran
        zs = body.get("zeroshield") or {}
        assert zs.get("action") == "redact", f"zeroshield.action={zs.get('action')!r}"
        assert zs.get("threat_type") == "pii", f"threat_type={zs.get('threat_type')!r}"
        stage = next(s for s in body["pipeline_trace"]["stages"]
                     if s.get("name") == "output_guardrail")
        assert stage.get("action") == "redact", f"stage.action={stage.get('action')!r}"
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_output_scan_disabled_bypasses_the_guard(monkeypatch):
    """Negative control: with per-org output_scan_enabled=false the guard is inert,
    proving the redaction observed elsewhere really is §1.7 and not another stage."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "block"},
        org_over={"output_scan_enabled": False})
    _set_upstream(_completion(f"Customer SSN {SSN} confirmed."))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "lookup"}]})
    assert resp.status_code == 200
    assert SSN in resp.json()["choices"][0]["message"]["content"]
    await auth_redis.aclose()


@pytest.mark.xfail(strict=True, reason=(
    "FINDING M17-05 (LOW/INFO, confirmed 2026-07-19): §1.7 claims hallucination-risk "
    "inspection, but on the stock SDK chat path there is no retrieved RAG context, so "
    "score_hallucination() takes its no-context branch (grounding_score=1.0) and a "
    "maximally over-confident fabrication ('I am absolutely certain, without any "
    "doubt ... I guarantee it 100%') yields verdict=('allow','',''). Every configured "
    "output_hallucination_action (flag/block/rewrite) is therefore unobservable "
    "through the SDK."))
@pytest.mark.asyncio
async def test_hallucination_action_is_observable_via_sdk(monkeypatch):
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_hallucination_action": "block",
                                "hallucination_flag_enabled": True})
    over_confident = ("I am absolutely certain, without any doubt, that the answer "
                     "is 42. I guarantee it 100% and it is definitely always true.")
    _set_upstream(_completion(over_confident))
    resp = await _raw_post(app, {"model": "gpt-4o-mini",
                                 "messages": [{"role": "user", "content": "fact"}]})
    try:
        assert resp.status_code == 400, (
            "configured hallucination block delivered the answer verbatim: "
            f"{resp.status_code}")
    finally:
        await auth_redis.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 4. STREAMING (ASGI) — action parity between stream and non-stream
# ═══════════════════════════════════════════════════════════════════════════
async def _stream_body(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                 headers={"Authorization": f"Bearer {T.API_KEY}"}) as c:
        return await c.post("/v1/chat/completions", json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "go"}],
            "stream": True})


@pytest.mark.xfail(strict=True, reason=(
    "FINDING M17-04 (HIGH, confirmed 2026-07-19): configured `block` is honoured "
    "non-streaming but silently downgraded to surgical `redact` when stream=true. "
    "IDENTICAL config (output_pii_action=block, output_credential_action=block) and "
    "IDENTICAL model output 'Your credential is AKIA... keep it safe.' produce: "
    "  stream=False -> status=400 code=output_blocked, nothing delivered "
    "  stream=True  -> status=200, delivered 'Your credential is AKIA****MPLE keep it safe.' "
    "A caller can therefore convert a hard output block into a partial delivery by "
    "flipping stream=true. The secret itself is still masked, so this is a policy "
    "bypass / enforcement-parity defect rather than a raw-data leak."))
@pytest.mark.asyncio
async def test_streaming_honours_configured_block_like_non_streaming(monkeypatch):
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "block",
                                "output_credential_action": "block"})
    gm.LLM_ROUTER.acompletion_stream = _split_stream(
        f"Your credential is {AKIA} keep it safe.", 6)
    resp = await _stream_body(app)
    try:
        assert resp.status_code == 400, (
            f"stream=true delivered a block-configured response: "
            f"{resp.status_code} {_sse_content(resp.text)!r}")
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_streaming_block_still_never_emits_the_raw_secret(monkeypatch):
    """Companion to M17-04: the downgrade is a policy defect, NOT a leak — assert
    the raw credential never reaches the wire even on the downgraded path."""
    app, auth_redis = await _guard_app(
        monkeypatch, guard_cfg={"output_pii_action": "block",
                                "output_credential_action": "block"})
    gm.LLM_ROUTER.acompletion_stream = _split_stream(
        f"Your credential is {AKIA} keep it safe.", 6)
    resp = await _stream_body(app)
    assert _canaries_in(resp.text) == [], _canaries_in(resp.text)
    await auth_redis.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 5. STREAMING over a REAL TCP SOCKET — chunk-boundary secret splitting
# ═══════════════════════════════════════════════════════════════════════════
_FAKE_SERVER = fakeredis.FakeServer()
_LIVE = {"stream": None}


def _apply_live_stubs():
    """Mirror test_openai_sdk_compat_live_uvicorn._apply_stubs, but with a REAL
    OutputGuard and a mutable upstream-stream slot the tests swap per case."""
    from ai_mesh_gateway.output_guard import OutputGuard

    sync_r = fakeredis.FakeStrictRedis(server=_FAKE_SERVER, decode_responses=True)
    sync_r.set(f"auth:apikey:{hashlib.sha256(T.API_KEY.encode()).hexdigest()}",
               json.dumps(T._auth_payload()))

    async def _get_redis(self):
        return fakeredis.aioredis.FakeRedis(server=_FAKE_SERVER, decode_responses=True)
    gw_middleware.AuthMiddleware._get_redis = _get_redis

    cfg = dict(T.TEST_CONFIG)
    cfg.update({"output_guard_enabled": True, "output_scan_enabled": True,
                "output_pii_action": "redact", "output_credential_action": "redact"})
    org_config = dict(cfg)
    config_sync = MagicMock()
    config_sync.get_config = MagicMock(return_value=org_config)
    config_sync.get_model_routing = MagicMock(
        return_value=[dict(T.TEST_MODEL), dict(T.EMBED_MODEL)])
    config_sync.reload_models_now = AsyncMock()

    async def _dispatch_stream(body, redacted_prompt=None, metrics=None, **kw):
        async for frame in _LIVE["stream"](body, redacted_prompt, metrics=metrics, **kw):
            yield frame

    router = MagicMock()
    router.acompletion = AsyncMock(side_effect=T._fake_completion)
    router.acompletion_stream = _dispatch_stream
    router.aembedding = AsyncMock(side_effect=T._fake_embedding)
    router.get_model_list = MagicMock(return_value=[{"id": "gpt-4o-mini"}])
    # Routing governance sizes the prompt + reads fallback chains; a bare MagicMock
    # returns a non-JSON-serialisable MagicMock once it reaches the zeroshield envelope.
    router.estimate_prompt_tokens = MagicMock(return_value=500)
    config_sync.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})

    gm.CONFIG = cfg
    gm.CONFIG_SYNC = config_sync
    gm.LLM_ROUTER = router
    gm.INPUT_SCANNER = InputScanner(thread_pool_size=2)
    gm.OUTPUT_GUARD = OutputGuard(gm.INPUT_SCANNER, cfg)
    gm.AGENT_ID = None
    gm.POLICY_SYNC = None
    gm.RATE_LIMITER = None
    gm.CIRCUIT_BREAKER = None
    gm.REDIS_CLIENT = None
    gm.TELEMETRY = None
    gm._emit_telemetry = lambda **_k: None
    gm._audit_fire_and_forget = lambda **_k: None
    try:
        gm.app.router.on_startup.clear()
        gm.app.router.on_shutdown.clear()
    except Exception:  # noqa: BLE001 - best effort, mirrors the sibling harness
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_url():
    _apply_live_stubs()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        gm.app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    assert getattr(server, "started", False), "uvicorn live server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret,chunk_size", [
    (AKIA, 1),    # every character in its own SSE frame
    (AKIA, 3),    # boundary lands mid-key
    (AKIA, 7),
    (AKIA, 4096),  # whole answer in one frame (control)
    (SSN, 2),
    (LONG_KEY, 7),  # 303-char key: forces a buffer-limit flush mid-secret
])
async def test_live_stream_secret_split_across_chunk_boundaries_is_masked(
        live_url, secret, chunk_size):
    """CRITICAL path: a secret whose characters straddle SSE chunk boundaries must
    still be caught. Driven over a real TCP socket so chunked-transfer framing —
    not ASGITransport's in-memory concatenation — is what the guard sees."""
    full = f"Your credential is {secret} please store it safely and do not share."
    _LIVE["stream"] = _split_stream(full, chunk_size)
    async with httpx.AsyncClient(base_url=live_url, timeout=20,
                                 headers={"Authorization": f"Bearer {T.API_KEY}"}) as c:
        resp = await c.post("/v1/chat/completions", json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "go"}],
            "stream": True})
    assert resp.status_code == 200, resp.text
    delivered = _sse_content(resp.text)
    assert secret not in delivered, (
        f"chunk_size={chunk_size}: raw secret reassembled by the client: {delivered!r}")
    assert secret not in resp.text, "raw secret present in the SSE wire body"
    assert "credential is" in delivered, f"answer was destroyed, not masked: {delivered!r}"


@pytest.mark.asyncio
async def test_live_stream_secret_at_the_very_end_is_masked(live_url):
    """The final-flush edge: a secret abutting the end of the stream (no trailing
    text to force a sentence-boundary flush) must still be masked at DONE."""
    _LIVE["stream"] = _split_stream(f"The access key is {AKIA}", 5)
    async with httpx.AsyncClient(base_url=live_url, timeout=20,
                                 headers={"Authorization": f"Bearer {T.API_KEY}"}) as c:
        resp = await c.post("/v1/chat/completions", json={
            "model": "gpt-4o-mini", "messages": [{"role": "user", "content": "go"}],
            "stream": True})
    assert resp.status_code == 200
    assert AKIA not in resp.text, "secret at stream end escaped the guard"
    assert "AKIA****MPLE" in _sse_content(resp.text)


@pytest.mark.asyncio
async def test_live_stream_stays_sdk_parseable_after_masking(live_url):
    """The masked stream must still be consumable by the stock SDK: chunks parse,
    ``[DONE]`` terminates, and finish_reason survives the guard's rewriting."""
    _LIVE["stream"] = _split_stream(f"Key {AKIA} is stored.", 3)
    client = openai.AsyncOpenAI(base_url=f"{live_url}/v1", api_key=T.API_KEY,
                                max_retries=0)
    try:
        stream = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "go"}],
            stream=True)
        chunks = [c async for c in stream]
        content = "".join(
            c.choices[0].delta.content or "" for c in chunks
            if c.choices and c.choices[0].delta)
        assert AKIA not in content, content
        assert "AKIA****MPLE" in content, content
        finish = [c.choices[0].finish_reason for c in chunks
                  if c.choices and c.choices[0].finish_reason]
        assert "stop" in finish, f"finish_reason lost after guard mutation: {finish}"
    finally:
        await client.close()
