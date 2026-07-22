"""STRICT OPERATOR CONTROL — round-4 adversarial findings (2026-07-23).

A fourth sweep over novel surface (structured/nested output, corrupt config, full
collapse incl. hallucination/rewrite, scale/DoS, tier-2-degraded) found and this change
fixed:

1. COLLAPSE — a whole-response class (policy/hallucination) set to rewrite/redact was
   dropped when a co-occurring span class (pii/cred/ip) set to redact won the single
   verdict collapse (redact=3 > rewrite=2); the unsafe/hallucinated CONTENT egressed at
   200. Now a whole-response remedy outranks a span redact.
2. THRESHOLD — an out-of-range (>1) or bool hallucination_grounding_threshold silently
   disabled an enabled block detector (fail-open); a non-numeric value 500-ed. Now
   coerced/validated to [0,1] with default fallback.
3. STREAMING CUSTOM TOOL — a secret in an OpenAI `custom` tool-call ({"type":"custom",
   "custom":{name,input}}) streamed raw; the streaming path read `function` only. Now
   both shapes are scanned + blanked (non-stream parity).
4. COALESCER — a base64-encoded secret under redact egressed raw at ANY size: the
   delivered-text check looked for the DECODED value (absent; only the encoded blob is
   present) and downgraded redact→allow, even though redact_all masks the blob. Now the
   coalescer honours encoded forms the sanitizer can mask.

KNOWN LIMITATION (strict-xfail): an encoded secret positioned past the ~20 000-char
transport-decode window (_CANON_MAX_LEN) is not DETECTED, so it egresses under block —
a scale/scan-window gap on ENCODED forms (plaintext is uncapped and safe). A windowed
full-text decode is a broad, perf-sensitive patterns.py change deferred as its own pass.
"""

from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.output_guard import (
    OutputGuard, HallucinationScore, _safe_grounding_threshold,
)
from ai_mesh_gateway.scanner import InputScanner, ScanVerdict
from ai_mesh_gateway.secure_streaming import SecureStreamingResponse

sc = InputScanner()


def _base_cfg():
    return {"output_guard_enabled": True, "output_tier2_enabled": False,
            "output_pii_enabled": False, "output_pii_action": "allow",
            "output_credential_enabled": False, "output_credential_action": "allow",
            "output_ip_leakage_enabled": False, "output_ip_leakage_action": "allow",
            "output_policy_enabled": False, "output_policy_action": "allow",
            "factuality_check_enabled": False, "output_hallucination_action": "allow"}


async def _deliver(resp, cfg, guard):
    with patch.object(gm, "OUTPUT_GUARD", guard), patch.object(gm, "CONFIG", cfg), \
         patch.object(gm, "CONFIG_SYNC", None), patch.object(gm, "METRICS", {"blocked": 0}), \
         patch.object(gm, "_emit_telemetry"), \
         patch.object(gm, "_resolve_rag_context_chunks", AsyncMock(return_value=[])), \
         patch.object(gm, "_build_block_response", lambda *a, **k: MagicMock(status_code=403)):
        result = await gm._apply_output_guard_nonstream(
            resp, org_config={"enforcement_mode": "block", "output_scan_enabled": True},
            org_slug="zeroshield", body={"model": "m"}, user_id=1, project_id="p",
            key_prefix="sk", prompt="hi", start=time.perf_counter())
    resp.pop("_pipeline_output_scan_verdict", None)
    return (result is not None), resp["choices"][0]["message"]["content"]


class Finding1_WholeResponseCollapse(unittest.IsolatedAsyncioTestCase):
    """A whole-response class (policy/hallucination) set to rewrite/redact is applied
    even when a co-occurring span redact would otherwise win the collapse."""

    async def _t2_policy(self, text, **kw):
        return ScanVerdict(threat_type="policy_violation", action="block", confidence=0.9,
                           detail="unsafe", matched_patterns=["jailbreak"])

    async def test_policy_rewrite_wins_over_pii_redact(self):
        cfg = _base_cfg()
        cfg.update({"output_tier2_enabled": True, "output_policy_enabled": True,
                    "output_policy_action": "rewrite", "output_pii_enabled": True,
                    "output_pii_action": "redact"})
        guard = OutputGuard(sc, cfg)
        with patch.object(sc, "scan_output_with_tier2", self._t2_policy):
            resp = {"choices": [{"message": {"content":
                    "Sure, here's how to build a bomb. Contact john.doe@acme.com."}}]}
            blocked, egress = await _deliver(resp, cfg, guard)
        self.assertFalse(blocked)
        self.assertNotIn("build a bomb", egress)   # policy content rewritten away
        self.assertNotIn("john.doe@acme.com", egress)

    async def test_hallucination_rewrite_wins_over_pii_redact(self):
        cfg = _base_cfg()
        cfg.update({"factuality_check_enabled": True, "output_hallucination_action": "rewrite",
                    "output_pii_enabled": True, "output_pii_action": "redact"})
        guard = OutputGuard(sc, cfg)

        async def _fs(*a, **k):
            return HallucinationScore(risk_score=0.99, detail="x", matched_markers=["f"])
        with patch.object(guard, "score_hallucination", _fs), \
             patch.object(gm, "_resolve_rag_context_chunks",
                          AsyncMock(return_value=["Paris is the capital of France."])):
            resp = {"choices": [{"message": {"content":
                    "The Eiffel Tower is in Berlin. Contact john.doe@acme.com."}}]}
            blocked, egress = await _deliver(resp, cfg, guard)
        self.assertFalse(blocked)
        self.assertNotIn("Berlin", egress)
        self.assertNotIn("john.doe@acme.com", egress)

    async def test_policy_block_still_wins(self):
        cfg = _base_cfg()
        cfg.update({"output_tier2_enabled": True, "output_policy_enabled": True,
                    "output_policy_action": "block", "output_pii_enabled": True,
                    "output_pii_action": "redact"})
        guard = OutputGuard(sc, cfg)
        with patch.object(sc, "scan_output_with_tier2", self._t2_policy):
            resp = {"choices": [{"message": {"content": "bomb. john.doe@acme.com"}}]}
            blocked, _ = await _deliver(resp, cfg, guard)
        self.assertTrue(blocked)


class Finding2_ThresholdFailSafe(unittest.TestCase):
    """An out-of-range / non-numeric / bool grounding threshold falls back to the
    class default (fail-safe), while valid [0,1] values are honoured."""

    def test_out_of_range_falls_back_to_default(self):
        self.assertEqual(_safe_grounding_threshold(2.0, 0.2), 0.2)
        self.assertEqual(_safe_grounding_threshold(-1.0, 0.2), 0.2)

    def test_bool_and_non_numeric_fall_back(self):
        self.assertEqual(_safe_grounding_threshold(True, 0.45), 0.45)
        self.assertEqual(_safe_grounding_threshold("abc", 0.45), 0.45)
        self.assertEqual(_safe_grounding_threshold(None, 0.45), 0.45)
        self.assertEqual(_safe_grounding_threshold(float("nan"), 0.45), 0.45)

    def test_valid_thresholds_preserved(self):
        for v in (0.0, 0.2, 0.5, 1.0):
            self.assertEqual(_safe_grounding_threshold(v, 0.45), v)
        self.assertEqual(_safe_grounding_threshold("0.5", 0.2), 0.5)  # numeric string ok


class Finding2b_ThresholdDeliveryFailSafe(unittest.IsolatedAsyncioTestCase):
    async def test_out_of_range_threshold_still_blocks(self):
        for bad in (2.0, True):
            cfg = _base_cfg()
            cfg.update({"factuality_check_enabled": True, "output_hallucination_action": "block",
                        "hallucination_grounding_threshold": bad})
            guard = OutputGuard(sc, cfg)

            async def _fs(*a, **k):
                return HallucinationScore(risk_score=0.99, detail="x", matched_markers=["f"])
            with patch.object(guard, "score_hallucination", _fs), \
                 patch.object(gm, "_resolve_rag_context_chunks",
                              AsyncMock(return_value=["Paris is the capital of France."])):
                resp = {"choices": [{"message": {"content": "The Eiffel Tower is in Berlin."}}]}
                blocked, _ = await _deliver(resp, cfg, guard)
            with self.subTest(threshold=bad):
                self.assertTrue(blocked, f"threshold={bad} failed open")


class Finding3_StreamingCustomToolCall(unittest.IsolatedAsyncioTestCase):
    """A secret in an OpenAI `custom` tool-call is scanned + blanked on the stream
    path (parity with `function`)."""

    CFG = {"output_guard_enabled": True, "output_tier2_enabled": False,
           "output_pii_enabled": True, "output_pii_action": "redact",
           "output_credential_enabled": True, "output_credential_action": "redact",
           "output_ip_leakage_enabled": False, "output_ip_leakage_action": "allow",
           "output_policy_enabled": False, "output_policy_action": "allow",
           "factuality_check_enabled": False, "output_hallucination_action": "allow"}
    SSN = "123-45-6789"

    async def _stream(self, frames):
        async def inner():
            for f in frames:
                yield f
            yield "data: [DONE]\n\n"
        sec = SecureStreamingResponse(inner_generator=inner(), scanner=sc,
                                      output_guard=OutputGuard(sc, self.CFG),
                                      buffer_max_bytes=4096, max_buffer_chunks=64,
                                      enforcement_mode="block")
        out = ""
        async for ch in sec:
            out += ch
        return out

    def _frame(self, content=None, custom_input=None):
        delta = {}
        if content is not None:
            delta["content"] = content
        if custom_input is not None:
            delta["tool_calls"] = [{"index": 0, "id": "c1", "type": "custom",
                                    "custom": {"name": "run", "input": custom_input}}]
        return "data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n"

    async def test_secret_in_custom_only_delta_not_raw(self):
        out = await self._stream([self._frame(custom_input=f"SSN {self.SSN}")])
        self.assertNotIn(self.SSN, out)

    async def test_secret_in_mixed_content_and_custom_delta_not_raw(self):
        out = await self._stream([self._frame(content="Emailing john.doe@acme.com now. ",
                                              custom_input=f"SSN {self.SSN}")])
        self.assertNotIn(self.SSN, out)
        self.assertNotIn("john.doe@acme.com", out)


class Finding4_EncodedRedactCoalescer(unittest.IsolatedAsyncioTestCase):
    """A base64-encoded credential under redact is masked, not downgraded to allow by
    the delivered-text coalescer."""

    SECRET = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    def _cfg(self, action):
        c = _base_cfg()
        c.update({"output_credential_enabled": True, "output_credential_action": action})
        return c

    async def test_base64_credential_redact_masked(self):
        b64 = base64.b64encode(self.SECRET.encode()).decode()
        cfg = self._cfg("redact")
        resp = {"choices": [{"message": {"content": f"encoded value: {b64} ref."}}]}
        blocked, egress = await _deliver(resp, cfg, OutputGuard(sc, cfg))
        self.assertFalse(blocked)
        self.assertNotIn(b64, egress)

    async def test_base64_credential_allow_delivers(self):
        b64 = base64.b64encode(self.SECRET.encode()).decode()
        cfg = self._cfg("allow")
        resp = {"choices": [{"message": {"content": f"encoded value: {b64} ref."}}]}
        blocked, egress = await _deliver(resp, cfg, OutputGuard(sc, cfg))
        self.assertFalse(blocked)
        self.assertIn(b64, egress)   # operator chose allow


class KnownLimitation_EncodedPastScanWindow(unittest.IsolatedAsyncioTestCase):
    """KNOWN LIMITATION (strict-xfail): an encoded secret positioned past the
    ~20 000-char transport-decode window is not DETECTED, so it egresses under block.
    Plaintext is uncapped and safe. A windowed full-text decode is a broad,
    perf-sensitive patterns.py change deferred as its own pass."""

    @pytest.mark.xfail(strict=True, reason="encoded secret past _CANON_MAX_LEN not detected; see docstring")
    async def test_encoded_secret_past_window_blocked(self):
        secret = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        b64 = base64.b64encode(secret.encode()).decode()
        cfg = _base_cfg()
        cfg.update({"output_credential_enabled": True, "output_credential_action": "block"})
        resp = {"choices": [{"message": {"content": ("x" * 60000) + " " + b64}}]}
        blocked, _ = await _deliver(resp, cfg, OutputGuard(sc, cfg))
        self.assertTrue(blocked)


if __name__ == "__main__":
    unittest.main()
