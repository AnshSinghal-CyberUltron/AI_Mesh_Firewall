"""G0 repro — redact verdict fires, raw phone still egresses (the documented gap).

This is the leak the B-stories close, captured as an executable RED test rather than
prose. It drives the REAL ``InputScanner`` + ``LLMRouter`` egress path (only the
provider faked) with a phone in a format the deterministic redactor misses while a
CO-OCCURRING value (email) makes redaction definitively fire.

Why these specific formats leak today (root cause, from llm_router.py):
  * ``patterns.redact_all`` only masks a bare 10-digit run behind an explicit phone
    cue and only as a CONTIGUOUS ``\\d{10}`` — a 5+5 spaced number ("89295 54991")
    matches no phone pattern.
  * the fail-closed digit backstop in ``_redact_text_with_backstop`` only catches
    CONTIGUOUS ``\\d{7,}`` runs, so a space between digit groups slips it too.
So the verdict (driven by the email) says "redaction fired", the trace shows a
masked body, but the raw phone rides to the upstream provider.

Marked ``xfail(strict=False)``: RED documents the gap (B2 broadens the redactor +
B1 byte-verifies the no-op-scrub fail-closed). When a B-story makes it pass it
XPASSes — the gate stays green either way, and the XPASS flags the fix landed.
"""
import pytest

from llm_router import LLMRouter
from scanner import InputScanner

import corpus
from recording_provider import RecordingProvider, independent_pii_scan


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


def _g0_item():
    return next(c for c in corpus.KNOWN_LEAKS if c.fmt == "us_5_5_spaced")


@pytest.mark.asyncio
async def test_g0_redact_verdict_but_raw_phone_egresses_is_documented():
    """Sanity (always green): the verdict fires AND, today, the phone is still on the
    wire raw — proving the G0 gap is real before we xfail-assert its closure."""
    phone = _g0_item()
    prompt = f"{phone.prompt()} and email evance.maps@mail.com about my order"

    scanner = InputScanner()
    verdict = await scanner.scan_prompt(prompt)
    # Redaction definitively fired (the email alone guarantees a non-None signal).
    redacted = scanner.redact_pii(prompt, verdict=verdict)
    assert redacted is not None and redacted != prompt
    # The gap: the redacted display STILL carries the raw 5+5 phone today.
    assert phone.raw in redacted, (
        "if redact_pii already masks the 5+5 phone, update the corpus 'covered' flag"
    )


@pytest.mark.asyncio
@pytest.mark.xfail(reason="G0/B2: 5+5 spaced phone misses redact_all + the digit "
                          "backstop; B2 broadens coverage, B1 byte-verifies.",
                   strict=False)
async def test_g0_no_raw_phone_on_wire(monkeypatch):
    phone = _g0_item()
    prompt = f"{phone.prompt()} and email evance.maps@mail.com about my order"

    scanner = InputScanner()
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)

    provider = RecordingProvider().install(monkeypatch)
    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted)
    assert status == 200

    # THE INVARIANT the B-stories must satisfy: the raw phone must not reach the wire.
    assert phone.raw not in provider.wire_blob, (
        f"raw phone egressed: oracle={independent_pii_scan(provider.wire_blob)!r}"
    )
