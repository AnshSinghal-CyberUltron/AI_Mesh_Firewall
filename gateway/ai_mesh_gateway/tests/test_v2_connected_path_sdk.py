"""CONNECTED-PATH revalidation of Module 1, through the STOCK ``openai`` SDK.

WHY THIS FILE EXISTS
--------------------
``main.py:7961`` short-circuits the whole ~1,900-line governance + output-guard
block when the worker has no routing catalogue AND no control-plane registration::

    _routing_catalogue_ready = bool(inference_models)
    if not _routing_catalogue_ready and (not AGENT_ID or not CONFIG["backend_url"]):
        ...  # forward straight upstream

Six of the eight M1.x SDK suites run with ``AGENT_ID = None`` and
``backend_url = ""``, i.e. on the STANDALONE side of that branch, where the
non-streaming output guard is ``_apply_output_guard_nonstream`` (main.py:2137).
The CONNECTED guard is a SECOND, independent implementation (main.py:8860-9600)
and had ZERO SDK coverage. Everything below drives the CONNECTED branch:
``AGENT_ID`` set, non-empty ``backend_url``, populated ``CONFIG_SYNC.get_model_routing``.

Run:
  cd gateway && .venv/bin/python -m pytest \
      ai_mesh_gateway/tests/test_v2_connected_path_sdk.py -q -p no:cacheprovider
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

from ai_mesh_gateway.tests import test_openai_sdk_compat as T

ORG = "org-connected-path"
MODEL = "gpt-4o-mini"

# ── canaries: distinct literals so an envelope-wide substring scan attributes a
# leak to the exact channel that carried it. ─────────────────────────────────
SSN = "412-55-9083"
AKIA = "AKIAIOSFODNN7EXAMPLE"
INTERNAL_IP = "10.42.7.19"

CATALOGUE = [
    {"model_name": MODEL, "model_id": MODEL, "provider": "openai",
     "is_active": True, "api_key_set": True},
    {"model_name": "gpt-4o", "model_id": "gpt-4o", "provider": "openai",
     "is_active": True, "api_key_set": True},
]


# ═══════════════════ envelope construction helpers ═══════════════════════════
def _msg(**over) -> dict:
    m = {"role": "assistant", "content": "All good."}
    m.update(over)
    return m


def _tool_call(args: str) -> list:
    return [{"id": "call_cp_1", "type": "function",
             "function": {"name": "send_report", "arguments": args}}]


def _logprobs(tokens: list[str]) -> dict:
    return {"content": [{"token": t, "logprob": -0.1, "bytes": list(t.encode()),
                         "top_logprobs": []} for t in tokens]}


def _completion(message: dict, *, choice_over: dict | None = None,
                top_over: dict | None = None) -> dict:
    choice = {"index": 0, "message": message, "finish_reason": "stop"}
    choice.update(choice_over or {})
    out = {
        "id": "chatcmpl-cp-001", "object": "chat.completion", "created": 1700000000,
        "model": MODEL, "choices": [choice],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
    }
    out.update(top_over or {})
    return out


class Upstream:
    """Records what the gateway asked upstream for; serves scripted responses."""

    def __init__(self, *responses):
        self.bodies: list[dict] = []
        self._responses = list(responses)

    async def acompletion(self, body, redacted_prompt=None, **_kw):
        self.bodies.append(json.loads(json.dumps(body, default=str)))
        idx = min(len(self.bodies) - 1, len(self._responses) - 1)
        return 200, json.loads(json.dumps(self._responses[idx]))

    @property
    def calls(self) -> int:
        return len(self.bodies)


# ═══════════════════ the CONNECTED-path fixture ══════════════════════════════
async def _build(monkeypatch, *, upstream, guard_cfg=None, allowed_models=None,
                 rate_limiter=None, connected=True, catalogue=None):
    """Mount the real app on the CONNECTED branch with a REAL OutputGuard.

    ``connected=False`` flips AGENT_ID/backend_url/catalogue off so the SAME
    input+config can be replayed on the STANDALONE branch for divergence tests.
    """
    from ai_mesh_gateway import main as gm, middleware as gw_middleware
    from ai_mesh_gateway.output_guard import OutputGuard
    from ai_mesh_gateway.scanner import InputScanner

    payload = dict(T._auth_payload(), org_slug=ORG, organization_id=ORG,
                   allowed_models=list(allowed_models if allowed_models is not None
                                       else [m["model_name"] for m in CATALOGUE]))
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await auth_redis.set(
        f"auth:apikey:{hashlib.sha256(T.API_KEY.encode('utf-8')).hexdigest()}",
        json.dumps(payload))

    async def _get_redis(self):
        return auth_redis
    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)

    cfg = dict(T.TEST_CONFIG)
    cfg["output_guard_enabled"] = True
    cfg["output_scan_enabled"] = True
    cfg["backend_url"] = "http://control-plane.invalid" if connected else ""
    cfg.update(guard_cfg or {})

    cs = MagicMock()
    # org_config (main.py:6142) and the guard's per-tenant config
    # (main.py:8906) both come from here, so one dict drives both.
    cs.get_config = MagicMock(return_value=dict(cfg))
    _cat = CATALOGUE if catalogue is None else catalogue
    cs.get_model_routing = MagicMock(
        return_value=[dict(m) for m in _cat] if connected else [])
    cs.reload_models_now = AsyncMock()
    cs.get_fallback_chains = MagicMock(return_value={"chains": {}, "per_primary": {}})

    lr = MagicMock()
    lr.acompletion = AsyncMock(side_effect=upstream.acompletion)
    lr.acompletion_stream = T._fake_stream
    lr.aembedding = AsyncMock(side_effect=T._fake_embedding)
    lr.get_model_list = MagicMock(return_value=[
        {"id": m["model_name"], "object": "model", "created": 1704067200,
         "owned_by": "openai"} for m in CATALOGUE])
    lr.estimate_prompt_tokens = MagicMock(return_value=50)

    scanner = InputScanner(thread_pool_size=2)
    telemetry: list[dict] = []

    for attr, val in {
        "CONFIG": dict(cfg), "CONFIG_SYNC": cs, "LLM_ROUTER": lr,
        "INPUT_SCANNER": scanner,
        "OUTPUT_GUARD": OutputGuard(scanner, dict(cfg)),
        "AGENT_ID": "agent-connected" if connected else None,
        "RATE_LIMITER": rate_limiter,
        "_emit_telemetry": lambda **k: telemetry.append(k),
        "_audit_fire_and_forget": lambda **_k: None,
        "_policy_check_cached": lambda *a, **k: (200, {}),
        **dict.fromkeys(("POLICY_SYNC", "CIRCUIT_BREAKER", "REDIS_CLIENT",
                         "TELEMETRY")),
    }.items():
        monkeypatch.setattr(gm, attr, val)

    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY, max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=gm.app),
                                      base_url="http://testserver"))
    return client, auth_redis, telemetry


@pytest_asyncio.fixture()
async def build(monkeypatch):
    made = []

    async def _f(**kw):
        client, redis, telemetry = await _build(monkeypatch, **kw)
        made.append((client, redis))
        return client, telemetry
    yield _f
    for _c, _r in made:
        await _c.close()
        await _r.aclose()


async def _raw(client, **over) -> httpx.Response:
    """Raw POST through the SDK's own http client — keeps the full envelope
    (headers + every non-schema field) available for substring scanning."""
    body = {"model": MODEL, "messages": [{"role": "user", "content": "status?"}]}
    body.update(over)
    return await client._client.post(
        "/v1/chat/completions", json=body,
        headers={"Authorization": f"Bearer {T.API_KEY}"})


def _canary_recoverable(resp: httpx.Response, canary: str = SSN) -> bool:
    """True when the client can recover ``canary`` from the response.

    A plain substring scan is NOT sufficient for ``logprobs``: a tokenizer splits
    "412-55-9083" into ("412-", "55-", "9083") and each token is a SEPARATE JSON
    string, so the contiguous canary never appears in ``resp.text`` even when the
    channel ships every byte of it. Reassemble the per-choice token sequence (and
    the parallel ``bytes`` arrays) the way a client trivially can."""
    if canary in resp.text:
        return True
    try:
        doc = resp.json()
    except Exception:  # noqa: BLE001
        return False
    for ch in (doc.get("choices") or []):
        lp = ch.get("logprobs") if isinstance(ch, dict) else None
        if not isinstance(lp, dict):
            continue
        for bucket in ("content", "refusal"):
            entries = [e for e in (lp.get(bucket) or []) if isinstance(e, dict)]
            if canary in "".join(str(e.get("token") or "") for e in entries):
                return True
            raw = bytearray()
            for e in entries:
                for b in (e.get("bytes") or []):
                    if isinstance(b, int):
                        raw.append(b)
            if canary in raw.decode("utf-8", "replace"):
                return True
    return False


def _zs_action(resp: httpx.Response) -> str | None:
    try:
        return ((resp.json().get("zeroshield") or {}).get("action"))
    except Exception:  # noqa: BLE001
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 0. The fixture must actually be on the connected branch
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_fixture_is_on_the_connected_branch(build):
    """Guard rail for every test below.

    ``RATE_LIMITER.check_model_rate_limit`` (main.py:8650) is UNREACHABLE from the
    standalone short-circuit — it lives ~700 lines past main.py:7961. Awaiting it
    is therefore positive proof the request executed the CONNECTED branch. The
    standalone half of the same assertion pins the branch discriminator itself."""
    rl = MagicMock()
    rl.check_model_rate_limit = AsyncMock(return_value=(True, 1))
    rl.check_rate_limit = AsyncMock(return_value=(True, 0))
    rl.check_org_rate_limit = AsyncMock(return_value=(True, 0))
    rl.record_usage = AsyncMock(return_value=None)
    cat = [dict(CATALOGUE[0], rate_limit_rpm=5), dict(CATALOGUE[1])]

    client, _tel = await build(upstream=Upstream(_completion(_msg())),
                               rate_limiter=rl, catalogue=cat)
    assert (await _raw(client)).status_code == 200
    assert rl.check_model_rate_limit.await_count == 1, (
        "connected-only governance never ran — the fixture is on the STANDALONE "
        "branch and every test in this file would be re-testing M1.x coverage")

    client2, _tel2 = await build(upstream=Upstream(_completion(_msg())),
                                 rate_limiter=rl, catalogue=cat, connected=False)
    assert (await _raw(client2)).status_code == 200
    assert rl.check_model_rate_limit.await_count == 1, (
        "standalone control ALSO reached the connected governance — the "
        "connected/standalone discriminator in this fixture is not real")


# ═══════════════════════════════════════════════════════════════════════════
# 1. I-01 ON THE CONNECTED GUARD — secondary delivered channels
#    PREVIOUSLY UNTESTED on this path (the I-01 fix was validated only against
#    _apply_output_guard_nonstream).
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("channel,message,choice_over", [
    ("tool_calls.arguments",
     _msg(content="Report sent.", tool_calls=_tool_call(f'{{"ssn": "{SSN}"}}')), None),
    ("reasoning_content",
     _msg(content="Report sent.", reasoning_content=f"user ssn is {SSN}"), None),
    ("refusal",
     _msg(content=None, refusal=f"I will not reveal {SSN}"), None),
    ("annotations",
     _msg(content="See note.",
          annotations=[{"type": "note", "text": f"ssn {SSN}"}]), None),
    ("logprobs",
     _msg(content="Report sent."),
     {"logprobs": _logprobs(["412-", "55-", "9083"])}),
])
async def test_i01_pii_only_in_secondary_channel_never_ships_raw(
        build, channel, message, choice_over):
    """I-01: PII present ONLY in a secondary CLIENT-DELIVERED channel, with clean
    ``content``, under ``output_pii_action=redact``. The raw canary must not
    appear ANYWHERE in the response envelope."""
    up = Upstream(_completion(message, choice_over=choice_over),
                  _completion(_msg(content="I cannot share that.")))
    client, _tel = await build(upstream=up, guard_cfg={"output_pii_action": "redact"})
    resp = await _raw(client, logprobs=bool(choice_over))
    assert not _canary_recoverable(resp), (
        f"CRITICAL: raw PII shipped to the client via {channel} on the CONNECTED "
        f"path: {resp.text[:1500]}")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,message,choice_over", [
    ("tool_calls.arguments",
     _msg(content="Report sent.", tool_calls=_tool_call(f'{{"ssn": "{SSN}"}}')), None),
    ("reasoning_content",
     _msg(content="Report sent.", reasoning_content=f"user ssn is {SSN}"), None),
    ("refusal",
     _msg(content=None, refusal=f"I will not reveal {SSN}"), None),
    ("annotations",
     _msg(content="See note.",
          annotations=[{"type": "note", "text": f"ssn {SSN}"}]), None),
    ("logprobs",
     _msg(content="Report sent."),
     {"logprobs": _logprobs(["412-", "55-", "9083"])}),
])
async def test_secondary_channels_really_are_client_delivered(
        build, channel, message, choice_over):
    """NEGATIVE CONTROL for the I-01 suite above.

    With the detector set to ``allow`` the canary MUST arrive intact. Without this
    control, an I-01 test could pass vacuously — the channel might simply be
    dropped in serialization rather than protected by the guard."""
    up = Upstream(_completion(message, choice_over=choice_over))
    client, _tel = await build(upstream=up, guard_cfg={"output_pii_action": "allow"})
    resp = await _raw(client, logprobs=bool(choice_over))
    assert resp.status_code == 200, resp.text
    assert _canary_recoverable(resp), (
        f"{channel} never reaches the client at all — the corresponding I-01 test "
        f"is vacuous, not a proof of containment: {resp.text[:800]}")


@pytest.mark.asyncio
async def test_i01_credential_only_in_tool_arguments_never_ships_raw(build):
    """Same as above for a credential canary (AKIA key) in tool arguments."""
    up = Upstream(_completion(_msg(content="Done.",
                                   tool_calls=_tool_call(f'{{"key": "{AKIA}"}}'))),
                  _completion(_msg(content="I cannot share that.")))
    client, _tel = await build(upstream=up,
                               guard_cfg={"output_credential_action": "redact",
                                          "output_pii_action": "redact"})
    resp = await _raw(client)
    assert AKIA not in resp.text, (
        f"CRITICAL: raw credential shipped via tool_calls.arguments: {resp.text[:1500]}")


@pytest.mark.asyncio
async def test_i01_secondary_channel_leak_is_reported_not_silent(build):
    """FINDING CP-02 (GAP, MEDIUM — fails SAFE).

    A tool-arguments-only PII match under ``output_pii_action=redact`` does not
    redact: it HARD-BLOCKS (400 ``output_blocked``). ``_sanitize_output_for_verdict``
    only rewrites the PRIMARY ``content`` (main.py:9070-9084), which is clean here,
    so ``_output_redact_redaction_possible`` sees an unchanged string and the
    post-redact ``_enforce_output`` fails closed. Containment is correct — the
    secret never ships — but the operator asked for redact and got block, and the
    tool call is destroyed rather than masked.

    Asserted as the OBSERVED behaviour so a future change to either outcome
    (silent allow == I-01 regression, or genuine tool-arg masking == the fix)
    trips this test."""
    up = Upstream(_completion(_msg(content="Report sent.",
                                   tool_calls=_tool_call(f'{{"ssn": "{SSN}"}}'))),
                  _completion(_msg(content="I cannot share that.")))
    client, tel = await build(upstream=up, guard_cfg={"output_pii_action": "redact"})
    resp = await _raw(client)
    assert SSN not in resp.text, "CRITICAL: raw PII escaped"
    assert resp.status_code == 400, resp.text[:600]
    assert resp.json().get("code") == "output_blocked", resp.text[:600]
    assert any(e.get("event_type") == "output_guard" for e in tel), (
        "no output_guard telemetry incident for a secondary-channel PII match")


# ═══════════════════════════════════════════════════════════════════════════
# 2. OUTPUT-GUARD ACTION FIDELITY on the connected path
#    PREVIOUSLY UNTESTED: M1.7 measured this on the standalone guard only.
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action,expect_status,raw_delivered", [
    # NOTE: a hard output block surfaces as HTTP 400 content_filter, not the 403
    # passed to _build_block_response — _build_safe_block_response normalises it
    # to the OpenAI error envelope (openai.BadRequestError for an SDK caller).
    ("block", 400, False),
    ("redact", 200, False),
    ("rewrite", 200, False),
    ("flag", 200, True),
])
async def test_action_fidelity_client_visible_matches_what_happened(
        build, action, expect_status, raw_delivered):
    """For each configured ``output_pii_action``: the HTTP status, the delivered
    content and the client-visible ``zeroshield.action`` must agree with each
    other AND with ``X-ZeroShield-Action``."""
    up = Upstream(_completion(_msg(content=f"Customer SSN {SSN} confirmed.")),
                  _completion(_msg(content="I cannot share that information.")))
    client, _tel = await build(upstream=up, guard_cfg={"output_pii_action": action})
    resp = await _raw(client)
    assert resp.status_code == expect_status, resp.text
    if expect_status != 200:
        assert SSN not in resp.text, "block envelope echoed the raw canary"
        return
    delivered = resp.json()["choices"][0]["message"]["content"]
    assert (SSN in delivered) is raw_delivered, delivered
    hdr = resp.headers.get("X-ZeroShield-Action")
    zs = _zs_action(resp)
    assert hdr is not None, "no X-ZeroShield-Action header on a guarded response"
    # 'redacted' is the header spelling of the 'redact' action.
    assert hdr.rstrip("ed").rstrip("e") == str(zs).rstrip("ed").rstrip("e"), (
        f"header {hdr!r} disagrees with zeroshield.action {zs!r}")


@pytest.mark.asyncio
async def test_rewrite_reinfers_through_the_router_on_connected_path(build):
    """``rewrite`` must be a genuine model re-inference (2 upstream calls), not a
    silent degradation to the static canned string."""
    up = Upstream(_completion(_msg(content=f"Customer SSN {SSN} confirmed.")),
                  _completion(_msg(content="I cannot share that information.")))
    client, _tel = await build(upstream=up, guard_cfg={"output_pii_action": "rewrite"})
    resp = await _raw(client)
    assert resp.status_code == 200, resp.text
    assert up.calls == 2, f"rewrite did not re-infer (upstream calls={up.calls})"
    assert SSN not in resp.text


@pytest.mark.asyncio
async def test_flag_delivers_but_records_an_incident(build):
    """``flag`` == deliver + record. Delivering WITHOUT an incident record would
    make the operator's choice unobservable."""
    up = Upstream(_completion(_msg(content=f"Customer SSN {SSN} confirmed.")))
    client, tel = await build(upstream=up, guard_cfg={"output_pii_action": "flag"})
    resp = await _raw(client)
    assert resp.status_code == 200
    assert SSN in resp.json()["choices"][0]["message"]["content"]
    assert any(e.get("event_type") == "output_guard" and e.get("action") == "flag"
               for e in tel), f"no flag incident telemetry: {[e.get('event_type') for e in tel]}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. PROVIDER-TOPOLOGY SCRUB (main.py:10078-10087) — connected branch ONLY.
#    PREVIOUSLY UNTESTED end-to-end through the SDK.
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_provider_topology_is_scrubbed_on_connected_path(build):
    """``provider``, ``citations``, ``usage.cost``, ``usage.is_byok`` and the RAW
    upstream model id must not reach the client."""
    raw = _completion(_msg(content="Fine."), top_over={
        "model": "anthropic/claude-3-5-sonnet-20241022",
        "provider": "Amazon Bedrock",
        "citations": ["https://internal.example/doc"],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14,
                  "cost": 0.00042, "is_byok": True},
    })
    up = Upstream(raw)
    client, _tel = await build(upstream=up)
    resp = await _raw(client)
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert "provider" not in doc, doc.get("provider")
    assert "citations" not in doc, doc.get("citations")
    assert "cost" not in doc["usage"] and "is_byok" not in doc["usage"], doc["usage"]
    assert doc["model"] == MODEL, f"raw upstream model id leaked: {doc['model']}"
    assert "Amazon Bedrock" not in resp.text
    assert "internal.example" not in resp.text
    assert "claude-3-5-sonnet-20241022" not in resp.text


@pytest.mark.asyncio
async def test_provider_specific_fields_scrubbed_on_connected_path(build):
    """``provider_specific_fields`` / ``native_finish_reason`` are upstream-family
    tells; they must be stripped at choice and message level."""
    msg = _msg(content="Fine.")
    msg["provider_specific_fields"] = {"native_finish_reason": "end_turn"}
    up = Upstream(_completion(msg, choice_over={"native_finish_reason": "end_turn"}))
    client, _tel = await build(upstream=up)
    resp = await _raw(client)
    assert resp.status_code == 200, resp.text
    assert "provider_specific_fields" not in resp.text, resp.text[:800]
    assert "native_finish_reason" not in resp.text, resp.text[:800]


# ═══════════════════════════════════════════════════════════════════════════
# 4. FINAL ALLOWLIST RE-CHECK + PER-MODEL RATE LIMITS (main.py:8603, :8646)
#    Both live ONLY in the connected branch — PREVIOUSLY UNTESTED via the SDK.
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_final_allowlist_recheck_blocks_non_allowlisted_model(build):
    """A model in the org catalogue but NOT on the key's allowlist must be
    rejected 403 ``model_not_allowed``, and upstream must never be called."""
    up = Upstream(_completion(_msg()))
    client, _tel = await build(upstream=up, allowed_models=[MODEL])
    resp = await _raw(client, model="gpt-4o")
    assert resp.status_code == 403, resp.text
    # normalised into the OpenAI error envelope by the error middleware
    assert resp.json()["error"]["code"] == "model_not_allowed", resp.text
    assert up.calls == 0, "non-allowlisted model still reached the upstream"


@pytest.mark.asyncio
async def test_per_model_rate_limit_runs_on_connected_path(build):
    """``RATE_LIMITER.check_model_rate_limit`` is only reachable past the
    short-circuit. Deny it and assert the 429 + Retry-After contract."""
    rl = MagicMock()
    rl.check_model_rate_limit = AsyncMock(return_value=(False, 999))
    rl.check_rate_limit = AsyncMock(return_value=(True, 0))
    rl.check_org_rate_limit = AsyncMock(return_value=(True, 0))
    rl.record_usage = AsyncMock(return_value=None)
    cat = [dict(CATALOGUE[0], rate_limit_rpm=5), dict(CATALOGUE[1])]
    up = Upstream(_completion(_msg()))
    client, _tel = await build(upstream=up, rate_limiter=rl, catalogue=cat)
    resp = await _raw(client)
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "model_rate_limit", resp.text
    assert resp.headers.get("Retry-After") == "60"
    assert up.calls == 0
    rl.check_model_rate_limit.assert_awaited()


@pytest.mark.asyncio
async def test_per_model_rate_limit_allows_under_the_cap(build):
    """The same wiring must PASS when the limiter allows — proves the 429 above
    came from the limiter, not from an unrelated connected-path rejection."""
    rl = MagicMock()
    rl.check_model_rate_limit = AsyncMock(return_value=(True, 1))
    rl.check_rate_limit = AsyncMock(return_value=(True, 0))
    rl.check_org_rate_limit = AsyncMock(return_value=(True, 0))
    rl.record_usage = AsyncMock(return_value=None)
    cat = [dict(CATALOGUE[0], rate_limit_rpm=5), dict(CATALOGUE[1])]
    up = Upstream(_completion(_msg()))
    client, _tel = await build(upstream=up, rate_limiter=rl, catalogue=cat)
    resp = await _raw(client)
    assert resp.status_code == 200, resp.text
    assert up.calls == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONNECTED vs STANDALONE DIVERGENCE on identical input + config
# ═══════════════════════════════════════════════════════════════════════════
async def _both_paths(build, *, message, guard_cfg, choice_over=None, **req):
    out = {}
    for name, connected in (("connected", True), ("standalone", False)):
        up = Upstream(_completion(message, choice_over=choice_over),
                      _completion(_msg(content="I cannot share that.")))
        client, tel = await build(upstream=up, guard_cfg=guard_cfg,
                                  connected=connected)
        resp = await _raw(client, **req)
        out[name] = resp
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["redact", "flag", "rewrite"])
async def test_paths_agree_on_delivered_content(build, action):
    """The two non-streaming guards must deliver the SAME bytes for the same
    input+config. A divergence is a real finding (the operator's config means
    one thing on a registered worker and another on an unregistered one)."""
    r = await _both_paths(build, message=_msg(content=f"Customer SSN {SSN} confirmed."),
                          guard_cfg={"output_pii_action": action})
    con, std = r["connected"], r["standalone"]
    assert con.status_code == std.status_code, (
        f"status divergence: connected={con.status_code} standalone={std.status_code}")
    c_txt = con.json()["choices"][0]["message"]["content"]
    s_txt = std.json()["choices"][0]["message"]["content"]
    assert c_txt == s_txt, f"delivered-content divergence:\n  {c_txt!r}\n  {s_txt!r}"


@pytest.mark.asyncio
async def test_paths_agree_on_block_status_code(build):
    """Both guards must return the SAME status for a hard output block."""
    r = await _both_paths(build, message=_msg(content=f"Customer SSN {SSN} confirmed."),
                          guard_cfg={"output_pii_action": "block"})
    con, std = r["connected"], r["standalone"]
    assert SSN not in con.text and SSN not in std.text
    assert con.status_code == std.status_code, (
        f"block-status divergence: connected={con.status_code} "
        f"standalone={std.status_code}")


@pytest.mark.asyncio
async def test_paths_agree_on_tool_channel_leak_containment(build):
    """I-01 parity: both guards must contain a tool-arguments-only PII leak."""
    r = await _both_paths(
        build, message=_msg(content="Report sent.",
                            tool_calls=_tool_call(f'{{"ssn": "{SSN}"}}')),
        guard_cfg={"output_pii_action": "redact"})
    leaks = {k: (SSN in v.text) for k, v in r.items()}
    assert not any(leaks.values()), f"raw PII shipped on: {leaks}"
    assert r["connected"].status_code == r["standalone"].status_code, (
        "the two guards disagree on how to enforce a tool-arguments-only leak: "
        f"connected={r['connected'].status_code} "
        f"standalone={r['standalone'].status_code}")


@pytest.mark.xfail(strict=True, reason=(
    "FINDING CP-01 (GAP, MEDIUM): the provider-topology scrub is INSIDE the "
    "connected branch (main.py:10078-10087) and has no counterpart on the "
    "standalone return at main.py:8012+. An unregistered worker with an empty "
    "catalogue therefore ships upstream 'provider' ('Amazon Bedrock'), "
    "'citations' and usage.cost/usage.is_byok verbatim to the client. Observed: "
    "provider topology leaked on: ['standalone'] (connected=False, standalone=True)."))
@pytest.mark.asyncio
async def test_topology_scrub_parity_between_paths(build):
    """The provider-topology scrub sits INSIDE the connected branch
    (main.py:10078). Does the standalone branch scrub too?"""
    results = {}
    for name, connected in (("connected", True), ("standalone", False)):
        up = Upstream(_completion(_msg(content="Fine."), top_over={
            "provider": "Amazon Bedrock",
            "usage": {"prompt_tokens": 8, "completion_tokens": 6,
                      "total_tokens": 14, "cost": 0.00042, "is_byok": True}}))
        client, _tel = await build(upstream=up, connected=connected)
        results[name] = (await _raw(client)).text
    leaked = {k: ("Amazon Bedrock" in v or "is_byok" in v)
              for k, v in results.items()}
    assert not any(leaked.values()), (
        f"provider topology leaked on: {[k for k, v in leaked.items() if v]}")
