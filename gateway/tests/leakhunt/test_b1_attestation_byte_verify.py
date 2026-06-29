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
