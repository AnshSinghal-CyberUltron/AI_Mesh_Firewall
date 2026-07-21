"""Streaming must obey the SAME fail-closed floor as the non-stream path.

Streaming never called ``enforce_output``, so two non-stream safety rules simply
did not exist on the stream path:

1. ``scan_degraded`` (a tier-2 output-guard OUTAGE) resolves ``allow -> redact``.
   Streaming only emitted an observability event and then honoured
   ``verdict.action``, so an outage FAILED OPEN — the unscanned response streamed
   out verbatim.
2. ``redact`` selected but nothing maskable resolves to ``block``
   (``redaction_possible=False``). Streaming downgraded it to ``flag`` and
   released the response, shipping the exact bytes the operator asked to remove.

Both are safety FLOORS, not action overrides: every per-detector action the
operator selected still passes through untouched (see the flag/allow controls
below and test_stream_strict_operator_actions.py for the full strict matrix).
"""

from __future__ import annotations

import json
import unittest

from ai_mesh_gateway.output_guard import OutputVerdict
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.secure_streaming import SecureStreamingResponse


PII_TEXT = "Contact john.doe@acme.com now."
# Tier-2 semantic PII: a real name no deterministic regex can span-match, so the
# redactor is a strict no-op over it.
UNMASKABLE_TEXT = "The patient is Margarethe Villanueva-Okonkwo, seen at the annex."


def _sse(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


class _FixedGuard:
    """Duck-typed output guard returning one operator-configured verdict."""

    def __init__(self, verdict: OutputVerdict) -> None:
        self._verdict = verdict

    async def inspect(self, text: str, **kwargs):  # noqa: ANN003, ARG002
        return self._verdict


async def _stream(text: str, verdict: OutputVerdict, chunk: int = 24) -> tuple[str, bool]:
    """Return (delivered_body, blocked).

    ``chunk`` controls how many segments the guard sees. _FixedGuard returns the
    SAME verdict for every segment, which a real guard never does — it derives the
    verdict from the segment it was handed. So a multi-segment run with a "pii
    redact" stub asserts PII in tail segments that contain none, which correctly
    (but artificially) trips the un-maskable->block floor. Tests that exercise the
    ordinary redact path therefore stream a single segment.
    """

    async def inner():
        for i in range(0, len(text), chunk):
            yield _sse(text[i : i + chunk])
        yield "data: [DONE]\n\n"

    secured = SecureStreamingResponse(
        inner_generator=inner(),
        scanner=InputScanner(),
        output_guard=_FixedGuard(verdict),
        buffer_max_bytes=1_000_000,
        max_buffer_chunks=1000,
        enforcement_mode="block",
    )

    body: list[str] = []
    blocked = False
    async for chunk in secured:
        if "Response blocked" in chunk:
            blocked = True
        if chunk.startswith("data: ") and "[DONE]" not in chunk:
            try:
                payload = json.loads(chunk[6:].strip())
            except ValueError:
                continue
            content = (payload.get("choices") or [{}])[0].get("delta", {}).get("content")
            if content:
                body.append(content)
    return "".join(body), blocked


class StreamScanDegradedFailsClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_degraded_redacts_instead_of_streaming_raw(self):
        verdict = OutputVerdict(action="allow", threat_type="", scan_degraded=True)
        body, blocked = await _stream(PII_TEXT, verdict)
        self.assertNotIn("john.doe@acme.com", body)
        self.assertFalse(blocked)

    async def test_no_degradation_leaves_allow_untouched(self):
        """The floor must not fire when the guard is healthy — allow means allow."""
        verdict = OutputVerdict(action="allow", threat_type="", scan_degraded=False)
        body, blocked = await _stream(PII_TEXT, verdict)
        self.assertEqual(body, PII_TEXT)
        self.assertFalse(blocked)


class StreamUnmaskableRedactFailsClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_unmaskable_redact_blocks_rather_than_shipping_raw(self):
        verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            confidence=0.9,
            detail="tier-2 semantic pii with no regex span",
            matched_patterns=["semantic_pii"],
        )
        body, blocked = await _stream(UNMASKABLE_TEXT, verdict)
        self.assertTrue(blocked)
        self.assertNotIn("Margarethe Villanueva-Okonkwo", body)

    async def test_maskable_redact_still_redacts_and_delivers(self):
        """Regression guard: the escalation must not turn ordinary redact into block."""
        verdict = OutputVerdict(
            action="redact",
            threat_type="pii",
            confidence=0.9,
            detail="email in output",
            matched_patterns=["email"],
        )
        body, blocked = await _stream(PII_TEXT, verdict, chunk=len(PII_TEXT))
        self.assertFalse(blocked)
        self.assertNotIn("john.doe@acme.com", body)
        self.assertIn("Contact", body)

    async def test_flag_still_delivers_byte_identical(self):
        """F-003: flag is deliver + telemetry. No floor may touch it."""
        verdict = OutputVerdict(
            action="flag",
            threat_type="pii",
            confidence=0.5,
            detail="email in output",
            matched_patterns=["email"],
        )
        body, blocked = await _stream(PII_TEXT, verdict)
        self.assertFalse(blocked)
        self.assertEqual(body, PII_TEXT)


if __name__ == "__main__":
    unittest.main()
