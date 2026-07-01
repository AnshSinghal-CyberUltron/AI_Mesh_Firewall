"""G0 closure — the 5+5 spaced phone no longer egresses raw (was the documented gap).

Originally this file captured a RED repro: a redact verdict fired (driven by a
co-occurring email) yet a 5+5 spaced phone ("89295 54991") rode to the upstream
provider RAW, because:
  * ``patterns.redact_all`` masked a bare 10-digit run only as a CONTIGUOUS
    ``\\d{10}`` behind a phone cue — a space between groups matched no pattern; and
  * the fail-closed digit backstop only caught CONTIGUOUS ``\\d{7,}`` runs.

B2-redactor-coverage broadened the context-gated ``phone_us_bare_contextual``
pattern (``_BARE_PHONE_10_SPLIT``) to also catch separator-split 10-digit phones
behind a phone cue, so the verdict and the wire now agree. These tests are the
GREEN regression proving the closure holds — drive the REAL ``InputScanner`` +
``LLMRouter`` egress path (only the provider faked).
"""
import pytest

from llm_router import LLMRouter
from scanner import InputScanner

import corpus
from recording_provider import RecordingProvider, independent_pii_scan


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


def _g0_item():
    """The former G0 leak (5+5 spaced phone), now covered by B2."""
    return next(c for c in corpus.CORPUS if c.fmt == "us_5_5_spaced")


@pytest.mark.asyncio
async def test_g0_5plus5_phone_is_masked_by_redactor():
    """The 5+5 spaced phone behind a phone cue is now masked by redact_pii (closure)."""
    phone = _g0_item()
    assert phone.covered, "us_5_5_spaced should be marked covered once B2 lands"
    prompt = f"{phone.prompt()} and email evance.maps@mail.com about my order"

    scanner = InputScanner()
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)
    assert redacted is not None and redacted != prompt
    # The gap is closed: the raw 5+5 phone is gone from the redacted display.
    assert phone.raw not in redacted, (
        f"5+5 phone still present after redaction: {redacted!r}"
    )


@pytest.mark.asyncio
async def test_g0_no_raw_phone_on_wire(monkeypatch):
    """The raw 5+5 phone must not reach the upstream wire (egress == verdict)."""
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

    assert phone.raw not in provider.wire_blob, (
        f"raw phone egressed: oracle={independent_pii_scan(provider.wire_blob)!r}"
    )
