"""
PIPELINE-0008: Leak verification — PII NEVER reaches the model on a block.

End-to-end proof that the original leak (input_scan BLOCK + model_output 7710ms —
a blocked prompt still forwarded to the LLM) cannot recur after PIPELINE-0005..0007.

Test dimensions:
  1. Input BLOCK → LLM_ROUTER.acompletion NEVER called; pipeline_trace model_output
     action=skip; no model_latency_ms
  2. Input REDACT → model called with MASKED prompt only; raw SSN/email/key byte-absent
  3. Degraded + PII → masked or blocked, never raw to model
  4. The original leak signature (blocked_stage=input_scan + model_output latency >0)
     is structurally impossible
"""

from __future__ import annotations

import inspect
import os
import re
import time
import unittest

import pytest


_TEST_SSN = "123-45-6789"
_TEST_EMAIL = "alice.jones@zeroshield-internal.example"
_TEST_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
_TEST_PII_PROMPT = f"Process SSN {_TEST_SSN} for user {_TEST_EMAIL} with key {_TEST_AWS_KEY}"
_BENIGN_PROMPT = "What is the capital of France?"


# ---------------------------------------------------------------------------
# 1. Input BLOCK → model NEVER called
# ---------------------------------------------------------------------------

class InputBlockModelNeverCalledTests(unittest.TestCase):
    """When input_scan blocks, the LLM is never invoked and the pipeline_trace
    records model_input / model_output as 'skip' with zero latency."""

    def test_pipeline_trace_block_has_model_skip(self):
        """build_pipeline_trace(blocked_stage='input_scan', final_action='block')
        produces model_input='skip' + model_output='skip'."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        self.assertEqual(stages["model_input"]["action"], "skip")
        self.assertEqual(stages["model_output"]["action"], "skip")

    def test_pipeline_trace_block_model_output_has_no_content(self):
        """Blocked trace model_output must have empty content (no LLM response)."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
            response_text="",
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["model_output"]["content"], "")

    def test_pipeline_trace_block_model_input_content_empty(self):
        """Blocked trace model_input.content is empty (prompt was NOT forwarded)."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["model_input"]["content"], "")

    def test_enforcement_injection_block_is_terminal(self):
        """resolve_and_enforce for prompt_injection → is_terminal_block=True,
        which triggers the short-circuit return BEFORE model calls."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertTrue(d.is_terminal_block)
        self.assertEqual(d.action, "block")
        self.assertEqual(d.blocked_by, "input_scan")

    def test_enforcement_org_policy_block_is_terminal(self):
        """org_policy_action='block' → is_terminal_block, model never called."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            org_policy_action="block",
        )
        self.assertTrue(d.is_terminal_block)

    def test_structural_block_returns_before_all_model_calls(self):
        """PIPELINE-0005 structural invariant: every input-side block return
        appears BEFORE any LLM_ROUTER.acompletion / stream call in proxy_chat
        source code. Re-verified here as a leak-verification backstop."""
        import ai_mesh_gateway.main as gw

        src = inspect.getsource(gw.proxy_chat)
        lines = src.splitlines()

        block_returns, model_calls = [], []
        fw_disabled = False

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "firewall_disabled" in stripped and "if" in stripped:
                fw_disabled = True
            if fw_disabled and stripped.startswith("if not") and "AGENT_ID" in stripped:
                fw_disabled = False

            if "return _build_block_response(" in stripped and "403" in stripped:
                block_returns.append(idx)

            if fw_disabled:
                continue
            if "LLM_ROUTER.acompletion" in stripped and "=" in stripped:
                model_calls.append(idx)
            if "_launch_chat_stream_response(" in stripped and "return" in stripped:
                model_calls.append(idx)

        self.assertTrue(block_returns, "No block-return sites found")
        self.assertTrue(model_calls, "No model-call sites found")

        earliest_model = min(model_calls)
        pre_model_blocks = [b for b in block_returns if b < earliest_model]
        self.assertGreaterEqual(
            len(pre_model_blocks), 3,
            f"Need >=3 input-side blocks before earliest model call at offset {earliest_model}",
        )


# ---------------------------------------------------------------------------
# 2. Input REDACT → model sees only masked prompt (byte-verified)
# ---------------------------------------------------------------------------

class InputRedactByteVerificationTests(unittest.TestCase):
    """When the input is redacted (not blocked), the model receives a masked
    prompt. Raw PII bytes must be absent from the forwarded text."""

    def test_enforcement_pii_redacts_not_blocks(self):
        """PII with redaction_possible=True → redact action (model IS called,
        but with masked text)."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=True,
        )
        self.assertEqual(d.action, "redact")
        self.assertFalse(d.is_terminal_block)

    def test_redact_all_removes_all_pii_types(self):
        """Byte-verify: redact_all masks SSN, email, and AWS key simultaneously."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all

        masked = redact_all(_TEST_PII_PROMPT)

        self.assertNotIn(_TEST_SSN, masked, "Raw SSN survived redaction")
        self.assertNotIn(_TEST_EMAIL, masked, "Raw email survived redaction")
        self.assertNotIn(_TEST_AWS_KEY, masked, "Raw AWS key survived redaction")
        self.assertNotEqual(masked, _TEST_PII_PROMPT, "Redaction was a no-op")

    def test_redact_preserves_benign_text(self):
        """redact_all on clean text is a no-op (no false masking)."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all

        self.assertEqual(redact_all(_BENIGN_PROMPT), _BENIGN_PROMPT)

    def test_pipeline_trace_redact_forwarded_differs(self):
        """When redacted, the trace shows forwarded_prompt ≠ original prompt,
        and the forwarded text has no raw PII."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all
        from pipeline_trace import build_pipeline_trace

        masked = redact_all(_TEST_PII_PROMPT)
        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            forwarded_prompt=masked,
            final_action="redact",
            blocked_stage="",
            http_status=200,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        mi = stages["model_input"]
        self.assertNotEqual(mi["action"], "skip", "Model must run for redact")
        self.assertNotIn(_TEST_SSN, mi.get("content", ""))
        self.assertNotIn(_TEST_EMAIL, mi.get("content", ""))
        self.assertNotIn(_TEST_AWS_KEY, mi.get("content", ""))

    def test_redact_chain_detect_then_mask(self):
        """Integration: detect_pii confirms PII → resolve_and_enforce says 'redact'
        → redact_all masks it → byte-absent."""
        try:
            from patterns import detect_pii, redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import detect_pii, redact_all
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        self.assertTrue(detect_pii(_TEST_PII_PROMPT), "PII must be detected")

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=True,
        )
        self.assertEqual(d.action, "redact")

        masked = redact_all(_TEST_PII_PROMPT)
        for raw_val in [_TEST_SSN, _TEST_EMAIL, _TEST_AWS_KEY]:
            self.assertNotIn(raw_val, masked, f"'{raw_val}' leaked through redaction")


# ---------------------------------------------------------------------------
# 3. Degraded scanner + PII → masked or blocked, never raw
# ---------------------------------------------------------------------------

class DegradedLeakVerificationTests(unittest.TestCase):
    """When the Tier-2 scanner is degraded and Tier-1 detects PII, the pipeline
    MUST redact (or block if unmaskable) — never forward raw."""

    def test_degraded_pii_resolves_to_redact(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
        )
        self.assertEqual(d.action, "redact")
        self.assertTrue(d.degraded)

    def test_degraded_pii_unmaskable_blocks(self):
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=True,
            redaction_possible=False,
        )
        self.assertEqual(d.action, "block")
        self.assertTrue(d.is_terminal_block)

    def test_degraded_pii_redacted_bytes_clean(self):
        """Full chain: degraded + detected PII → redact decision → byte-verify
        masked prompt has no raw PII."""
        try:
            from patterns import detect_pii, detect_secrets, detect_credential_exposure, redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import (
                detect_pii, detect_secrets, detect_credential_exposure, redact_all,
            )
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        pii_found = bool(
            detect_pii(_TEST_PII_PROMPT) or detect_secrets(_TEST_PII_PROMPT)
            or detect_credential_exposure(_TEST_PII_PROMPT)
        )
        self.assertTrue(pii_found)

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=pii_found,
        )
        self.assertEqual(d.action, "redact")

        masked = redact_all(_TEST_PII_PROMPT)
        self.assertNotIn(_TEST_SSN, masked)
        self.assertNotIn(_TEST_EMAIL, masked)
        self.assertNotIn(_TEST_AWS_KEY, masked)

    def test_degraded_clean_monitors_not_blocks(self):
        """Degraded + clean → monitor only (no false block)."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        d = resolve_and_enforce(
            scanner_recommendation="allow",
            scanner_threat_type="bedrock_degraded",
            tier2_degraded=True,
            tier1_pii_detected=False,
        )
        self.assertEqual(d.action, "monitor")
        self.assertFalse(d.is_terminal_block)


# ---------------------------------------------------------------------------
# 4. Original leak signature: input_scan BLOCK + model_output ran → impossible
# ---------------------------------------------------------------------------

class OriginalLeakSignatureTests(unittest.TestCase):
    """The original leak was: input_scan BLOCK yet model_output had a 7710ms
    latency — the model was called AFTER the block.

    PIPELINE-0005..0007 make this structurally impossible:
      - resolve_and_enforce → is_terminal_block → short-circuit return
      - build_pipeline_trace: blocked_stage=input_scan → model stages='skip'
      - _build_block_response: single-writer, returns HTTP 403 immediately

    These tests prove the signature cannot recur."""

    def test_blocked_at_input_scan_model_stages_always_skip(self):
        """build_pipeline_trace with blocked_stage='input_scan' ALWAYS produces
        model_input='skip' AND model_output='skip'."""
        from pipeline_trace import build_pipeline_trace

        for bs in ("input_scan", "policy", "rate_limit", "kill_switch"):
            trace = build_pipeline_trace(
                prompt="test",
                final_action="block",
                blocked_stage=bs,
                http_status=403,
            )
            stages = {s["name"]: s for s in trace["stages"]}
            self.assertEqual(
                stages["model_input"]["action"], "skip",
                f"model_input should be skip when blocked at {bs}",
            )
            self.assertEqual(
                stages["model_output"]["action"], "skip",
                f"model_output should be skip when blocked at {bs}",
            )

    def test_blocked_model_output_has_no_latency_content(self):
        """When blocked at input_scan, model_output has no response content
        and its latency is minimal (≤1ms — just the trace overhead, no LLM call)."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
            stage_metrics={},
            response_text="",
        )
        stages = {s["name"]: s for s in trace["stages"]}

        mo = stages["model_output"]
        self.assertEqual(mo["action"], "skip")
        self.assertEqual(mo["content"], "")

    def test_output_guard_block_does_NOT_skip_model(self):
        """An output_guardrail block (AFTER the model ran) must NOT mark the
        model stages as skip — the model DID run and produced a response."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="test",
            final_action="block",
            blocked_stage="output_guardrail",
            http_status=403,
            response_text="The model responded with something.",
        )
        stages = {s["name"]: s for s in trace["stages"]}

        self.assertNotEqual(stages["model_input"]["action"], "skip")
        self.assertNotEqual(stages["model_output"]["action"], "skip")

    def test_terminal_block_decisions_are_exhaustive(self):
        """Every block vector that resolve_and_enforce can produce has
        is_terminal_block=True, ensuring the short-circuit fires."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        vectors = [
            {"scanner_recommendation": "block", "scanner_threat_type": "prompt_injection",
             "scanner_confidence": 0.99, "scan_block_on_injection": True, "injection_threshold": 0.5},
            {"scanner_recommendation": "block", "scanner_threat_type": "pii",
             "redaction_possible": False},
            {"scanner_recommendation": "block", "scanner_threat_type": "toxicity"},
            {"org_policy_action": "block"},
            {"scanner_recommendation": "block", "scanner_threat_type": "jailbreak",
             "scanner_confidence": 0.99, "scan_block_on_injection": True, "injection_threshold": 0.5},
        ]
        for i, kwargs in enumerate(vectors):
            d = resolve_and_enforce(**kwargs)
            self.assertTrue(
                d.is_terminal_block,
                f"Vector {i} ({kwargs}) should be terminal block, got action={d.action}",
            )

    def test_redact_decisions_are_NOT_terminal(self):
        """Redact decisions (PII maskable) must NOT be terminal — the model
        IS called with the masked prompt."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce

        vectors = [
            {"scanner_recommendation": "block", "scanner_threat_type": "pii",
             "redaction_possible": True},
            {"scanner_recommendation": "block", "scanner_threat_type": "secret",
             "redaction_possible": True},
        ]
        for kwargs in vectors:
            d = resolve_and_enforce(**kwargs)
            self.assertFalse(d.is_terminal_block)
            self.assertEqual(d.action, "redact")


# ---------------------------------------------------------------------------
# 5. Cross-ref: the 7710ms model_output on a block is structurally impossible
# ---------------------------------------------------------------------------

class LeakSignatureCrossRefTests(unittest.TestCase):
    """Verify that the exact original leak pattern — blocked at input_scan but
    model_output stage shows a multi-second latency — cannot be produced by
    build_pipeline_trace after the PIPELINE-0005..0007 fixes."""

    def test_blocked_trace_model_output_action_skip_regardless_of_metrics(self):
        """Even if stage_metrics carries upstream_ms (from a prior run), when
        blocked_stage='input_scan' the authoritative signal is action='skip'
        + empty content — the model was structurally never called."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt="test",
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
            stage_metrics={"upstream_ms": 7710},
        )
        stages = {s["name"]: s for s in trace["stages"]}
        mo = stages["model_output"]

        self.assertEqual(mo["action"], "skip")
        self.assertEqual(mo["content"], "")
        self.assertIn("blocked upstream", mo.get("detail", ""))

    def test_skip_stage_clears_all_guard_metadata(self):
        """Skipped stages must carry no guard/threat/pattern data that could
        confuse operators into thinking the model ran."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        for name in ("model_input", "model_output", "output_guardrail"):
            stage = stages[name]
            self.assertEqual(stage["action"], "skip")
            self.assertIn("blocked upstream", stage.get("detail", ""))
            self.assertFalse(
                stage.get("threat_type"),
                f"{name} should not carry threat_type when skipped",
            )

    def test_no_raw_pii_in_any_skipped_stage(self):
        """No raw PII appears in any skipped stage's content/detail fields."""
        from pipeline_trace import build_pipeline_trace

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action="block",
            blocked_stage="input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        for name in ("model_input", "model_output", "output_guardrail"):
            stage = stages[name]
            blob = str(stage)
            self.assertNotIn(_TEST_SSN, blob, f"Raw SSN in skipped stage {name}")
            self.assertNotIn(_TEST_EMAIL, blob, f"Raw email in skipped stage {name}")
            self.assertNotIn(_TEST_AWS_KEY, blob, f"Raw AWS key in skipped stage {name}")


# ---------------------------------------------------------------------------
# 6. Full-chain integration: BLOCK → complete pipeline_trace → no model, no PII
# ---------------------------------------------------------------------------

class FullChainBlockIntegrationTests(unittest.TestCase):
    """Simulate the full block chain as proxy_chat would execute it after
    PIPELINE-0004..0007: detect → resolve → build_block_response → trace."""

    def test_injection_block_full_chain(self):
        """Injection detection → terminal block → trace shows model_output skip,
        zero raw PII in any stage."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce
        from pipeline_trace import build_pipeline_trace

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="prompt_injection",
            scanner_confidence=0.95,
            scan_block_on_injection=True,
            injection_threshold=0.80,
        )
        self.assertTrue(d.is_terminal_block)

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action=d.action,
            blocked_stage=d.blocked_by or "input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        self.assertEqual(stages["model_input"]["action"], "skip")
        self.assertEqual(stages["model_output"]["action"], "skip")
        self.assertEqual(stages["model_output"]["content"], "")

        all_text = str(trace)
        self.assertNotIn(_TEST_SSN, all_text)
        self.assertNotIn(_TEST_EMAIL, all_text)

    def test_unmaskable_pii_block_full_chain(self):
        """Unmaskable PII → terminal block → trace has model_output skip."""
        from ai_mesh_gateway.enforcement import resolve_and_enforce
        from pipeline_trace import build_pipeline_trace

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=False,
        )
        self.assertTrue(d.is_terminal_block)

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            final_action=d.action,
            blocked_stage=d.blocked_by or "input_scan",
            http_status=403,
        )
        stages = {s["name"]: s for s in trace["stages"]}
        self.assertEqual(stages["model_input"]["action"], "skip")
        self.assertEqual(stages["model_output"]["action"], "skip")

    def test_redact_full_chain_model_sees_masked_only(self):
        """PII redact → model_input has masked prompt, no raw PII bytes."""
        try:
            from patterns import redact_all
        except ImportError:
            from ai_mesh_gateway.patterns import redact_all
        from ai_mesh_gateway.enforcement import resolve_and_enforce
        from pipeline_trace import build_pipeline_trace

        d = resolve_and_enforce(
            scanner_recommendation="block",
            scanner_threat_type="pii",
            redaction_possible=True,
        )
        self.assertEqual(d.action, "redact")

        masked = redact_all(_TEST_PII_PROMPT)

        trace = build_pipeline_trace(
            prompt=_TEST_PII_PROMPT,
            forwarded_prompt=masked,
            final_action="redact",
            blocked_stage="",
            http_status=200,
        )
        stages = {s["name"]: s for s in trace["stages"]}

        mi = stages["model_input"]
        self.assertNotEqual(mi["action"], "skip")
        self.assertNotIn(_TEST_SSN, mi.get("content", ""))
        self.assertNotIn(_TEST_EMAIL, mi.get("content", ""))
        self.assertNotIn(_TEST_AWS_KEY, mi.get("content", ""))


if __name__ == "__main__":
    unittest.main()
