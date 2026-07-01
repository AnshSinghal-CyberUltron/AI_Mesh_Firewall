"""B1 — verdict-scrub honesty / egress-truth invariant on the input/egress path.

PRIME INVARIANT (progress.txt): *egress = truth*. A redact verdict is valid ONLY
if the bytes the gateway actually sends upstream reflect it. The deterministic
per-message redactor used on the wire (``patterns.redact_all`` + the digit backstop
in ``llm_router._redact_text_with_backstop``) is, by construction, WEAKER than the
firewall's authoritative ``redacted_content`` (``InputScanner.redact_pii``): a value
the firewall decided to mask can still slip ``redact_all`` (e.g. a separator-split
phone) and ride to the provider RAW — while the trace shows it masked. That is a
phantom redaction.

B1 closes it at the egress choke point: any sensitive run the firewall removed
(absent from ``redacted_content``) must NOT survive raw on the wire — if the weaker
redactor was a no-op on that span, fail closed (stronger scrub). FP-safety is
structural: we mask ONLY runs the firewall already removed, so a value the firewall
deliberately KEPT (a legit order id) is never over-redacted.

These tests drive the REAL ``LLMRouter`` egress path (chat + responses) with only
the upstream provider faked, and assert the wire reflects the verdict.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from llm_router import LLMRouter

from recording_provider import RecordingProvider, independent_pii_scan


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


# ── Chat: a span the firewall masked must never survive raw on the wire ──
@pytest.mark.asyncio
async def test_b1_chat_firewall_masked_span_never_egresses(monkeypatch):
    """The firewall masked the 5+5 spaced phone (absent from redacted_content), but
    the weaker per-message ``redact_all`` misses that format. Without B1 the raw
    phone rides to the provider while the verdict says redact (phantom redaction)."""
    raw_phone = "89295 54991"
    prompt = f"please call me at {raw_phone} about my order"
    # Firewall's authoritative decision: phone masked, rest intact.
    redacted_content = "please call me at ***-***-4991 about my order"

    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted_content)
    assert status == 200
    assert raw_phone not in provider.wire_blob, (
        "phantom redaction: firewall masked the phone but raw egressed; "
        f"oracle={independent_pii_scan(provider.wire_blob)!r}"
    )


@pytest.mark.asyncio
async def test_b1_chat_multiturn_masked_span_never_egresses(monkeypatch):
    """Egress-truth holds across EARLIER turns too — a masked run in any user turn
    must not ride raw."""
    raw_phone = "89295 54991"
    redacted_content = "earlier: call ***-***-4991 . now: continue"
    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": f"earlier: call {raw_phone}"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "now: continue"},
        ],
    }
    status, _ = await router.acompletion(body, redacted_content=redacted_content)
    assert status == 200
    assert raw_phone not in provider.wire_blob


# ── FP-safety: a value the firewall KEPT (legit order id) must NOT be over-redacted ──
@pytest.mark.asyncio
async def test_b1_value_firewall_kept_is_not_over_redacted(monkeypatch):
    """The firewall kept the order id (present in redacted_content) — B1 must not
    invent a redaction for it. Only the phone (which the firewall removed) is masked."""
    order_id = "12345678"
    raw_phone = "89295 54991"
    prompt = f"order {order_id} please call me at {raw_phone}"
    redacted_content = f"order {order_id} please call me at ***-***-4991"

    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted_content)
    assert status == 200
    assert raw_phone not in provider.wire_blob, "phone (firewall-removed) should be masked"
    assert order_id in provider.wire_blob, "order id (firewall-kept) must survive — no over-redaction"


# ── Contiguous digit backstop preserved (no regression of the existing behavior) ──
@pytest.mark.asyncio
async def test_b1_contiguous_digit_backstop_still_holds(monkeypatch):
    raw = "8929554991"  # contiguous 10-digit
    prompt = f"my number {raw} ok"
    redacted_content = "my number ***-***-4991 ok"
    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted_content)
    assert status == 200
    assert raw not in provider.wire_blob


# ── No redaction signal → body forwarded unchanged, no spurious masking ──
@pytest.mark.asyncio
async def test_b1_no_signal_forwards_unchanged(monkeypatch):
    prompt = "what is the capital of France? order 12345678"
    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=None)
    assert status == 200
    # Nothing flagged → the benign numeric run is untouched (no phantom masking).
    assert "12345678" in provider.wire_blob


# ── Responses: string input wire == firewall redaction; tool descriptions byte-verified ──
@pytest.mark.asyncio
async def test_b1_responses_string_input_wire_equals_firewall_redaction(monkeypatch):
    raw_phone = "89295 54991"
    redacted_content = "please call me at ***-***-4991"
    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    router._deployment_params = {"gpt-4o-mini": {"model": "gpt-4o-mini"}}
    body = {"model": "gpt-4o-mini", "input": f"please call me at {raw_phone}"}
    status, _ = await router.aresponses(body, redacted_content=redacted_content)
    assert status == 200
    assert raw_phone not in provider.wire_blob


# ── END-TO-END: the REAL chat endpoint must produce egress-truth redaction ──
# The router-level cells above hand-feed an already-correct ``redacted_content``, so
# they prove the ROUTER honors a good verdict — but NOT that main.py PRODUCES one.
# This cell drives the stock SDK against the live chat endpoint with a Tier-2 verdict
# whose evidence echoes a bare digit run the deterministic regexes (``redact_all``)
# miss. main.py must pass that VERDICT to ``redact_pii`` (so
# ``redact_evidence_digit_spans`` masks the run); otherwise ``redact_pii`` degrades to
# plain ``redact_all`` (scanner.py:854) and the raw digits ride to the provider while
# the verdict says "redact" — a phantom redaction the unit cells cannot see.
@pytest.mark.asyncio
async def test_b1_chat_endpoint_honors_tier2_evidence_digit_span(monkeypatch):
    _gw_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_gw_root))  # so the ``ai_mesh_gateway`` package resolves
    sys.path.insert(0, str(_gw_root / "ai_mesh_gateway" / "tests"))
    import test_openai_sdk_compat as H  # in-process app + auth + stock-SDK helpers
    # Use the SAME module object the harness patches (``ai_mesh_gateway.main``), not the
    # flat ``main`` the leakhunt conftest also exposes — they are distinct module objects.
    from ai_mesh_gateway import main as gateway_main
    from scanner import ScanVerdict

    acct = "4480293185"  # bare 10-digit: no phone/ssn/card shape -> redact_all misses it
    captured: dict = {}

    async def _capture_completion(body, redacted_prompt=None, **_kw):
        # ``redacted_prompt`` is exactly the bytes main.py forwards to the provider.
        captured["redacted_prompt"] = redacted_prompt
        return 200, {
            "id": "chatcmpl-b1e2e", "object": "chat.completion", "created": 1700000000,
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        }

    app, auth_redis = await H._make_sdk_app(monkeypatch, redis_client=None)

    async def _scan(text, *a, **k):
        # A Tier-2 guard flags a bare account number; evidence echoes the raw digits.
        return ScanVerdict(
            action="redact", threat_type="pii",
            matched_patterns=["llm_guard_pii"],
            scan_meta={"findings": [{"evidence": f"account {acct} flagged"}]},
        )

    monkeypatch.setattr(gateway_main.INPUT_SCANNER, "scan_prompt", _scan)
    monkeypatch.setattr(gateway_main.INPUT_SCANNER, "scan_prompt_with_tier2", _scan)
    monkeypatch.setattr(gateway_main.LLM_ROUTER, "acompletion",
                        AsyncMock(side_effect=_capture_completion))

    client = H._stock_client(app)
    try:
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"the reference value is {acct} per the audit"}],
        )
    finally:
        await auth_redis.aclose()

    red = captured.get("redacted_prompt")
    assert red is not None, "main.py forwarded a redact-verdict prompt without redacting"
    assert acct not in red, (
        "phantom redaction: verdict said redact but the raw bare digit span reached the "
        f"provider; redacted_prompt={red!r}"
    )


# ── END-TO-END: an UNMASKABLE flagged span must FAIL CLOSED, never ride raw ──
# Criterion 1: "if the scrubber is a no-op, fail closed (stronger scrub or block)."
# A Tier-2 guard recommending redaction surfaces as action="flag" /
# threat_type="sensitive_content" (scanner.py), which main.py routes through
# redact_pii. When the flagged value is a natural-language credential the
# deterministic redactors (redact_all + the digit backstop) cannot touch, the scrub
# is a TOTAL no-op — without the fail-closed honesty check the raw credential reached
# the provider while telemetry attested "redact" (a phantom redaction the embeddings
# path already guards, main.py:_scan_redact_embedding_inputs). This mirrors that
# guard onto the chat/egress path: the request is blocked and acompletion is never
# called, so nothing raw egresses.
@pytest.mark.asyncio
async def test_b1_chat_endpoint_fails_closed_on_unmaskable_flagged_span(monkeypatch):
    import sys
    from openai import APIStatusError

    _gw_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_gw_root))
    sys.path.insert(0, str(_gw_root / "ai_mesh_gateway" / "tests"))
    import test_openai_sdk_compat as H
    from ai_mesh_gateway import main as gateway_main
    from scanner import ScanVerdict

    # Oracle-confirmed PII (mcp aidefence_has_pii -> hasPII:true) that redact_all and
    # the digit backstop both leave UNCHANGED: a natural-language passphrase.
    secret = "correcthorsebatterystaple"
    captured: dict = {}

    async def _capture_completion(body, redacted_prompt=None, **_kw):
        captured["redacted_prompt"] = redacted_prompt
        return 200, {
            "id": "chatcmpl-b1fc", "object": "chat.completion", "created": 1700000000,
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        }

    app, auth_redis = await H._make_sdk_app(monkeypatch, redis_client=None)

    async def _scan(text, *a, **k):
        return ScanVerdict(
            action="flag", threat_type="sensitive_content",
            matched_patterns=["password"],
            scan_meta={"findings": [{"evidence": "credential present"}]},
        )

    monkeypatch.setattr(gateway_main.INPUT_SCANNER, "scan_prompt", _scan)
    monkeypatch.setattr(gateway_main.INPUT_SCANNER, "scan_prompt_with_tier2", _scan)
    monkeypatch.setattr(gateway_main.LLM_ROUTER, "acompletion",
                        AsyncMock(side_effect=_capture_completion))

    client = H._stock_client(app)
    blocked = False
    try:
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"the wifi password: {secret}"}],
        )
    except APIStatusError:
        blocked = True
    finally:
        await auth_redis.aclose()

    assert blocked, "unmaskable flagged credential must fail closed (block), not forward"
    # The block happens BEFORE egress — the provider is never called with the raw value.
    assert captured.get("redacted_prompt") is None, (
        "phantom redaction: provider was called despite an unmaskable flagged span"
    )


@pytest.mark.asyncio
async def test_b1_responses_tool_description_masked_span_never_egresses(monkeypatch):
    raw_phone = "89295 54991"
    redacted_content = "support hotline ***-***-4991"
    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    router._deployment_params = {"gpt-4o-mini": {"model": "gpt-4o-mini"}}
    body = {
        "model": "gpt-4o-mini",
        "input": "hi",
        "tools": [{
            "type": "function",
            "name": "call_support",
            "description": f"support hotline {raw_phone}",
        }],
    }
    status, _ = await router.aresponses(body, redacted_content=redacted_content)
    assert status == 200
    assert raw_phone not in provider.wire_blob
