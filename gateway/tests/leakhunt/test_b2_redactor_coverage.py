"""B2-redactor-coverage gate — phone/intl redactor coverage, context-gated.

Story B2 broadens phone redaction to neutralize the leaking formats (bare-10-digit,
5+5 spaced, +CC) WITHOUT over-redacting order-id / revenue numbers. The invariant
is *egress = truth*: when a phone arrives behind a phone cue, the raw value must be
absent from the bytes the gateway sends upstream; when an ambiguous bare run arrives
with NO phone cue (order id / revenue figure), it must pass through untouched.

These tests drive the REAL ``InputScanner`` + ``LLMRouter`` egress path (only the
upstream provider faked via the A2 ``RecordingProvider``) plus the deterministic
``patterns.redact_all`` for the false-positive assertions.
"""
import pytest

from llm_router import LLMRouter
from scanner import InputScanner

import corpus
from patterns import redact_all
from recording_provider import RecordingProvider, independent_pii_scan


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


# Every phone format the redactor is expected to neutralize (covered=True).
_COVERED_PHONES = [c for c in corpus.by_label("phone") if c.covered]
# Order-id / revenue style numbers that look phone-shaped but carry NO phone cue —
# masking these would be a false positive (the by-design pass-through class).
_FALSE_POSITIVE_SAMPLES = [
    "your order 8929554991 has shipped",
    "Q3 revenue was 8929554991 in local currency",
    "ref number 8929554991 for the ticket",
    "invoice 89295 54991 is now paid",
    "tracking id 4155550142 updated",
    "the account balance 1234567890 was reconciled",
]


@pytest.mark.parametrize("item", _COVERED_PHONES, ids=[c.fmt for c in _COVERED_PHONES])
@pytest.mark.asyncio
async def test_phone_format_not_on_wire(item, monkeypatch):
    """Each covered phone format, in its realistic cue, is absent from the egress wire."""
    prompt = f"{item.prompt()} — please follow up about my order"

    scanner = InputScanner()
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)

    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted)
    assert status == 200

    assert item.raw not in provider.wire_blob, (
        f"{item.fmt} ({item.raw!r}) egressed raw; "
        f"oracle={independent_pii_scan(provider.wire_blob)!r}"
    )


def test_redact_all_masks_5plus5_behind_cue():
    """The headline G0 format is masked by the deterministic redactor itself."""
    out = redact_all("please call me at 89295 54991 today")
    assert "89295 54991" not in out
    assert "89295" not in out and "54991" not in out


@pytest.mark.parametrize("text,raw", [
    ("sms 89295 54991 to confirm", "89295 54991"),
    ("whatsapp 8929554991 for updates", "8929554991"),
    ("dial 4155550142 now", "4155550142"),
    ("text 89295.54991", "89295.54991"),
    ("telegram +919876543210", "919876543210"),
    ("imessage 8929554991", "8929554991"),
])
def test_redact_all_masks_strong_cue_direct_adjacency(text, raw):
    """B2 rigor (sms/whatsapp direct-adjacency leak): a strong dialing/messaging verb
    placed IMMEDIATELY before the number (no "at/on" connector) used to ride RAW to the
    wire — the imperative branch demanded the connector and Tier-2 did not flag it."""
    out = redact_all(text)
    assert raw not in out
    assert "89295" not in out or raw == "4155550142" or raw == "919876543210"


@pytest.mark.parametrize("text", [
    "call 5000 customers about the promo",
    "text the 1234567890 line tomorrow",
    "we will call 89295 customers in batch 54991",
    "message 1234567890 was delivered",   # 'message' is NOT a direct-adjacency cue
    "contact 1234567890 records",         # 'contact' stays gated on at/on
])
def test_direct_adjacency_does_not_over_redact(text):
    """The direct-adjacency branch requires the 10-digit grouping to start RIGHT after a
    curated phone verb + whitespace/colon, so order-id / count phrasings are untouched."""
    assert redact_all(text) == text


def test_redact_all_masks_intl_5_5_grouping():
    """B2 rigor: a 5+5 grouped international mobile ("+91 98765 43210", India/EU) is
    masked by phone_intl. The grouped branch previously capped groups at 4 digits, so
    this common format leaked raw even though the docstring claimed coverage."""
    out = redact_all("my mobile is +91 98765 43210 today")
    assert "98765 43210" not in out
    assert "98765" not in out and "43210" not in out
    # documented sibling (2-2-4-4) must still mask
    assert "7946 0958" not in redact_all("+44 20 7946 0958 office")


@pytest.mark.parametrize("text", [
    "order 98765 43210 shipped",          # spaced groups, NO '+' => order id
    "Q3 revenue 91 98765 43210 in local", # leading country-ish digit but NO '+'
    "sku 12345 67890 reconciled",         # 5+5 with no '+' => part id
])
def test_intl_widening_does_not_over_redact(text):
    """The phone_intl widening is gated on a leading '+', so spaced numeric runs that
    are NOT international phone numbers (order ids / revenue / sku) are untouched."""
    assert redact_all(text) == text


@pytest.mark.parametrize("text", _FALSE_POSITIVE_SAMPLES)
def test_order_id_revenue_untouched(text):
    """No phone cue => the ambiguous numeric run survives redaction verbatim."""
    assert redact_all(text) == text


@pytest.mark.asyncio
async def test_order_id_not_redacted_on_wire(monkeypatch):
    """End-to-end: an order id behind no phone cue rides to the wire unchanged
    (proves B2 did not introduce an over-redaction regression)."""
    prompt = "your order 8929554991 has shipped, thanks"

    scanner = InputScanner()
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)
    # redact_pii returns the (possibly unchanged) text; the order id must remain.
    assert "8929554991" in (redacted or prompt)

    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted)
    assert status == 200
    assert "8929554991" in provider.wire_blob
