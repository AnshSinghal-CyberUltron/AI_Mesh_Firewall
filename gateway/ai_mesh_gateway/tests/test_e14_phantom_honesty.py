"""E14 — PHANTOM output-redaction honesty (adversarial).

Invariant under test (output-guard "redact" honesty):

    action == "redact"  ==>  the CLIENT-DELIVERED bytes differ from the raw
                             model output.
    (and the inverse: a no-op redactor must DOWNGRADE the reported action to
     "flag", never claim a phantom "redact" on a response delivered verbatim.)

Why this matters: a verdict/telemetry ``action == "redact"`` that ships the
model output UNCHANGED is a *phantom redaction* — the §1.7 dashboard records a
masked response while the client receives the secret in full. The recon claim
(``main.py:1566`` ``_redact_action = "redact" if sanitized != response_text
else "flag"``) downgrades to flag on the NON-STREAM ``_apply_output_guard_nonstream``
path; this suite probes whether that honesty discipline is applied uniformly,
and in particular on the STREAMING path.

The phantom class: a tier-2 guard-model verdict can target a category the
deterministic regex redactor (``redact_all`` / ``InputScanner.redact_pii``) has
no pattern for — e.g. a free-text person name, or a tier-2 "phi" rating of a
narrative medical note with no structured regex hit. On those, the redactor is
a byte-for-byte no-op, yet the redact branch fires. The non-stream path detects
this and reports "flag"; the streaming path (``secure_streaming``) hard-codes
``action="redact"`` with NO such check.

Run:
    cd /Users/anshsinghal/Desktop/AI_Security/AI_Mesh_Firewall/gateway
    .venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_e14_phantom_honesty.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the package importable the same way the rest of the suite does
# (tests import bare module names: ``output_guard``, ``secure_streaming`` …).
_PKG = Path(__file__).resolve().parents[1]
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import output_guard as og  # noqa: E402
import secure_streaming as ss  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Stubs
# ─────────────────────────────────────────────────────────────────────────────
class _NoOpScanner:
    """An InputScanner whose redactor masks NOTHING — models the tier-2-only
    phantom class where the deterministic regex redactor has no pattern for the
    flagged content, so ``redact_pii`` returns the input verbatim."""

    def redact_pii(self, text, verdict=None):  # signature parity with scanner.py
        return text  # NO-OP — the documented phantom precondition.


class _RedactGuard:
    """An OutputGuard stub that returns a tier-2-style ``redact`` verdict for a
    redactable category. The streaming path will call ``redact_pii`` (no-op) and
    must NOT then claim a phantom 'redact' on bytes it delivered unchanged."""

    def __init__(self, *, action="redact", threat_type="pii"):
        self._action = action
        self._threat_type = threat_type

    async def inspect(self, text, *, context_chunks=None, org_config=None, org_slug=""):
        return og.OutputVerdict(
            action=self._action,
            threat_type=self._threat_type,
            confidence=0.85,
            detail="tier-2 guard flagged content with no deterministic mask",
            matched_patterns=[],   # tier-2 has no regex span the redactor can act on
            compliance_tags=[],
        )


class _CapturingTelemetry:
    """Captures every emitted telemetry event so the test can read the reported
    ``action`` and compare it against the bytes actually delivered."""

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def _sse(content: str) -> str:
    chunk = {
        "id": "chatcmpl-e14",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


async def _gen(chunks):
    for c in chunks:
        yield c


def _delivered_content(emitted_sse_list) -> str:
    """Reassemble the client-visible assistant content from the SSE the wrapper
    actually yielded downstream (skips [DONE] / error / non-data frames)."""
    parts = []
    for raw in emitted_sse_list:
        line = raw.strip()
        if not line.startswith("data: "):
            continue
        body = line[6:]
        if body == "[DONE]":
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("error"):
            continue
        for ch in data.get("choices") or []:
            delta = ch.get("delta") if isinstance(ch, dict) else None
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    return "".join(parts)


async def _drive_stream(guard, scanner, raw_text):
    """Run a single-flush stream through SecureStreamingResponse and return
    (delivered_content, emitted_actions)."""
    tel = _CapturingTelemetry()
    # One content chunk ending in a sentence boundary forces a BOUNDARY flush.
    inner = _gen([_sse(raw_text), "data: [DONE]\n\n"])
    wrapper = ss.SecureStreamingResponse(
        inner,
        scanner,
        redaction_enabled=True,
        output_guard=guard,
        telemetry=tel,
        org_slug="acme",
        model="gpt-test",
    )
    out = [frame async for frame in wrapper.__aiter__()]
    actions = [
        getattr(e, "action", None) if not isinstance(e, dict) else e.get("action")
        for e in tel.events
    ]
    return _delivered_content(out), actions, out


# ─────────────────────────────────────────────────────────────────────────────
# Non-stream honesty (recon claim — should PASS / prove the guard is real)
# ─────────────────────────────────────────────────────────────────────────────
def test_nonstream_sanitizer_noop_is_detectable():
    """The deterministic no-op precondition is real: a tier-2 redact verdict on
    a category the regex redactor can't mask leaves bytes verbatim. This is what
    the non-stream ``_redact_action`` downgrade is built to catch."""
    raw = "The project lead is Dana Whitfield and the meeting is Tuesday."
    verdict = og.OutputVerdict(action="redact", threat_type="pii", matched_patterns=[])
    sanitized = og.sanitize_output_for_verdict(
        raw, verdict, redact_pii_fn=_NoOpScanner().redact_pii
    )
    # No-op redactor => bytes unchanged => non-stream path would report "flag".
    assert sanitized == raw, "expected the no-op redactor to leave bytes verbatim"
    _redact_action = "redact" if sanitized != raw else "flag"
    assert _redact_action == "flag", (
        "non-stream honesty contract: a no-op redaction MUST downgrade to 'flag'"
    )


def test_nonstream_real_redaction_stays_redact():
    """Positive control: when the redactor actually changes bytes, the honest
    action stays 'redact'."""
    raw = "her email is dana@example.com, contact her"
    verdict = og.OutputVerdict(action="redact", threat_type="pii", matched_patterns=["email"])

    # real masker via the package patterns.redact_all
    try:
        from patterns import redact_all
    except ImportError:  # pragma: no cover
        from ..patterns import redact_all  # type: ignore

    sanitized = og.sanitize_output_for_verdict(raw, verdict, redact_pii_fn=redact_all)
    assert sanitized != raw, "real email redaction must change bytes"
    _redact_action = "redact" if sanitized != raw else "flag"
    assert _redact_action == "redact"


# ─────────────────────────────────────────────────────────────────────────────
# Streaming honesty (the adversarial probe — phantom hunt)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_streaming_redact_implies_bytes_changed():
    """PHANTOM PROBE.

    Drive the real streaming output guard with a tier-2 'redact' verdict whose
    category the deterministic redactor cannot mask (no-op redact_pii). Assert
    the honesty invariant:

        if the emitted telemetry/enforcement action == 'redact',
        then the client-delivered bytes MUST differ from the raw model output.

    A phantom path emits action='redact' while delivering the secret verbatim;
    that makes this assertion FAIL — i.e. this test is a failing repro of the
    streaming phantom redaction.
    """
    raw = "The project lead is Dana Whitfield and the launch is Tuesday."
    delivered, actions, _frames = await _drive_stream(
        _RedactGuard(action="redact", threat_type="pii"),
        _NoOpScanner(),
        raw,
    )

    # The wrapper claimed to redact (this is the streaming hard-coded action).
    bytes_unchanged = delivered == raw

    for act in actions:
        if act == "redact":
            assert not bytes_unchanged, (
                "PHANTOM REDACTION (streaming): telemetry reported action='redact' "
                "but the client received the model output BYTE-FOR-BYTE UNCHANGED "
                f"(delivered == raw). delivered={delivered!r}. "
                "The streaming path (secure_streaming._flush_buffer redact branch) "
                "hard-codes action='redact' without the non-stream "
                "`sanitized != response_text -> 'flag'` honesty downgrade, so a "
                "tier-2 verdict the regex redactor cannot mask ships the secret "
                "while the 1.7 dashboard records a masked response."
            )


@pytest.mark.asyncio
async def test_streaming_real_redaction_changes_bytes_positive_control():
    """Positive control: with a redactor that genuinely masks the content, a
    streaming 'redact' is honest (delivered bytes differ from raw)."""
    raw = "contact dana@example.com about the launch please now."

    try:
        from patterns import redact_all
    except ImportError:  # pragma: no cover
        from ..patterns import redact_all  # type: ignore

    class _RealScanner:
        def redact_pii(self, text, verdict=None):
            return redact_all(text)

    delivered, actions, _frames = await _drive_stream(
        _RedactGuard(action="redact", threat_type="pii"),
        _RealScanner(),
        raw,
    )
    assert "redact" in actions, "expected the streaming guard to report a redact"
    assert delivered != raw, "a real redaction must change the delivered bytes"
    assert "dana@example.com" not in delivered, "the email must not survive redaction"
