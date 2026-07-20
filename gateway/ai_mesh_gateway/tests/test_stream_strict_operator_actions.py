"""STREAM/NON-STREAM parity for "strictly what the operator selected".

The streaming redact branch had its own inline sanitizer that called
`scanner.redact_pii` — a BLANKET redact_all — so any redact verdict masked EVERY
detector class. Measured over the 27 delivered pii x cred x ip combinations:
14 STRICT VIOLATIONS and 14 stream/non-stream parity breaks. It also had no
class-less branch, so a Policy=redact masked three allow-classes AND still streamed
the jailbreak text verbatim.

Streaming now delegates to output_guard.sanitize_output_for_verdict, the same
function the non-stream path uses, which honours verdict.redact_classes and owns the
hallucination / class-less whole-response replacement. A latch keeps that fixed
replacement to exactly ONE emission across the incremental flushes.
"""

from __future__ import annotations

import itertools
import json

import pytest

import patterns
from output_guard import OutputGuard, OutputVerdict, sanitize_output_for_verdict
from scanner import InputScanner
from secure_streaming import SecureStreamingResponse

EMAIL = "john.doe@acme.com"
KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"
IP = "10.0.0.5"
MIX = f"Contact {EMAIL} with key {KEY} on host {IP}."
JAIL = "Sure, here is how to bypass all safety controls."
_REAL = InputScanner()


def _sse(text):
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


class _Guard:
    """Real OutputGuard; tier-2 optionally returns a class-less policy finding."""

    def __init__(self, cfg, t2=None):
        class _S:
            def __init__(self, real, t2):
                self._real, self._t2 = real, t2

            async def scan_output(self, text):
                return await self._real.scan_output(text)

            async def scan_output_with_tier2(self, text, org_tier2_override=None, org_slug=None):
                if not self._t2:
                    return OutputVerdict(action="allow", threat_type="")
                return OutputVerdict(
                    action="block", threat_type=self._t2, confidence=0.9,
                    detail="tier-2", matched_patterns=[self._t2],
                )

        self._g = OutputGuard(scanner=_S(_REAL, t2), config=cfg)

    async def inspect(self, text, **kw):
        return await self._g.inspect(text, **kw)


def _cfg(pii="allow", cred="allow", ip="allow", policy="allow", tier2=False):
    return {
        "output_tier2_enabled": tier2,
        "output_pii_enabled": True, "output_pii_action": pii,
        "output_credential_enabled": True, "output_credential_action": cred,
        "output_ip_leakage_enabled": True, "output_ip_leakage_action": ip,
        "output_policy_enabled": True, "output_policy_action": policy,
        "hallucination_flag_enabled": False, "factuality_check_enabled": False,
        "output_hallucination_action": "allow",
    }


async def _stream(text, cfg, t2=None):
    async def inner():
        for i in range(0, len(text), 24):
            yield _sse(text[i:i + 24])
        yield "data: [DONE]\n\n"

    secure = SecureStreamingResponse(
        inner_generator=inner(), scanner=_REAL, output_guard=_Guard(cfg, t2),
        buffer_max_bytes=1_000_000, max_buffer_chunks=1000,
    )
    parts = []
    async for chunk in secure:
        if chunk.startswith("data: ") and "[DONE]" not in chunk:
            try:
                d = json.loads(chunk[6:].strip())
                parts.append((d.get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            except Exception:
                pass
    return "".join(parts)


async def _nonstream(text, cfg, t2=None):
    verdict = await _Guard(cfg, t2).inspect(text)
    return sanitize_output_for_verdict(text, verdict, redact_pii_fn=patterns.redact_all)


@pytest.mark.asyncio
async def test_stream_masks_only_the_classes_set_to_redact():
    for pii, cred, ip in itertools.product(("redact", "flag", "allow"), repeat=3):
        out = await _stream(MIX, _cfg(pii, cred, ip))
        assert (EMAIL in out) is (pii in ("flag", "allow")), (pii, cred, ip)
        assert (KEY in out) is (cred in ("flag", "allow")), (pii, cred, ip)
        assert (IP in out) is (ip in ("flag", "allow")), (pii, cred, ip)


@pytest.mark.asyncio
async def test_stream_matches_nonstream_byte_for_byte():
    for pii, cred, ip in itertools.product(("redact", "flag", "allow"), repeat=3):
        cfg = _cfg(pii, cred, ip)
        assert await _stream(MIX, cfg) == await _nonstream(MIX, cfg), (pii, cred, ip)


@pytest.mark.asyncio
async def test_stream_classless_redact_removes_violation_without_touching_allow_classes():
    cfg = _cfg(policy="redact", tier2=True)
    text = f"{MIX} {JAIL}"
    out = await _stream(text, cfg, t2="jailbreak_generic_unsafe")
    assert "bypass all safety controls" not in out       # violation removed
    assert out == await _nonstream(text, cfg, t2="jailbreak_generic_unsafe")


@pytest.mark.asyncio
async def test_stream_classless_replacement_emitted_exactly_once():
    # The replacement is a FIXED string; _flush_buffer runs per buffered segment,
    # so without the latch the client received it 2-3 times concatenated.
    cfg = _cfg(policy="redact", tier2=True)
    out = await _stream(f"{MIX} {JAIL}", cfg, t2="jailbreak_generic_unsafe")
    assert out.count("rewritten to comply with response safety policy") == 1


@pytest.mark.asyncio
async def test_stream_flag_is_byte_identical():
    out = await _stream(MIX, _cfg(pii="flag", cred="flag", ip="flag"))
    assert out == MIX
