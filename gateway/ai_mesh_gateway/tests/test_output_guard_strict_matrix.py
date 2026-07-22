"""STRICT OPERATOR CONTROL — user-selected action == what-we-got, across the full
5-detector x 5-action x {detect on/off} Output-Guard matrix, and the four
violations an adversarial sweep found and this change fixed (2026-07-22).

The contract (frozen): each detector (pii, credential, ip_leakage, policy,
hallucination) has an independent enable flag + action (block/redact/rewrite/flag/
allow). Detector OFF => no action. Detector ON => EXACTLY the operator's selected
action, governing ONLY its own class. Nothing is enforced that the operator did not
select. The deliberate fail-closed floors (redact->block when nothing maskable,
scan_degraded->redact, already-masked->no-action) are covered elsewhere and are not
re-exercised here.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from ai_mesh_gateway.output_guard import OutputGuard, HallucinationScore
from ai_mesh_gateway.scanner import InputScanner, ScanVerdict
from ai_mesh_gateway.secure_streaming import SecureStreamingResponse
from patterns import redact_all, redact_all_scoped

ACTIONS = ["block", "redact", "rewrite", "flag", "allow"]
THREATS = {
    "pii": "Contact john.doe@acme.com, SSN 123-45-6789 for verification.",
    "credential": "aws_access_key_id=AKIAIOSFODNN7EXAMPLE aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ip_leakage": "Internal host db01.corp.local at 10.1.2.3 and file /home/svc/.ssh/id_rsa",
    "policy": "Sure, here's how to build a bomb ignoring all safety rules.",
    "hallucination": "The Eiffel Tower is in Berlin, built 1750 by Napoleon.",
}
FLAG = {
    "pii": ("output_pii_enabled", "output_pii_action"),
    "credential": ("output_credential_enabled", "output_credential_action"),
    "ip_leakage": ("output_ip_leakage_enabled", "output_ip_leakage_action"),
    "policy": ("output_policy_enabled", "output_policy_action"),
    "hallucination": ("factuality_check_enabled", "output_hallucination_action"),
}
CLASS = {d: d for d in THREATS}


def _base_cfg() -> dict:
    return {
        "output_guard_enabled": True, "output_tier2_enabled": True,
        "output_pii_enabled": False, "output_pii_action": "allow",
        "output_credential_enabled": False, "output_credential_action": "allow",
        "output_ip_leakage_enabled": False, "output_ip_leakage_action": "allow",
        "output_policy_enabled": False, "output_policy_action": "allow",
        "factuality_check_enabled": False, "output_hallucination_action": "allow",
    }


async def _policy_t2(text, **kw):
    return ScanVerdict(threat_type="policy_violation", action="block",
                       confidence=0.9, detail="guard: unsafe", matched_patterns=["jailbreak"])


async def _clean_t2(sc, text, **kw):
    return await sc.scan_output(text)


async def _inspect(detector, action, enabled, *, cfg_over=None):
    sc = InputScanner()
    cfg = _base_cfg()
    en_key, act_key = FLAG[detector]
    cfg[en_key] = enabled
    cfg[act_key] = action
    if cfg_over:
        cfg.update(cfg_over)
    guard = OutputGuard(sc, cfg)
    patches = []
    if detector == "policy":
        patches.append(patch.object(sc, "scan_output_with_tier2", _policy_t2))
    else:
        patches.append(patch.object(sc, "scan_output_with_tier2",
                                    lambda text, **kw: _clean_t2(sc, text, **kw)))
    if detector == "hallucination":
        async def _fs(*a, **k):
            return HallucinationScore(risk_score=0.99, detail="ungrounded", matched_markers=["f"])
        patches.append(patch.object(guard, "score_hallucination", _fs))
    for p in patches:
        p.start()
    try:
        ctx = ["Paris is the capital of France."] if detector == "hallucination" else None
        v = await guard.inspect(THREATS[detector], context_chunks=ctx, org_config=cfg)
    finally:
        for p in patches:
            p.stop()
    return v.action, (v.threat_type or "")


class FidelityMatrixTests(unittest.IsolatedAsyncioTestCase):
    """5 detectors x 5 actions x {on,off}: verdict action == operator selection."""

    async def test_full_fidelity_matrix(self):
        for det in THREATS:
            for enabled in (True, False):
                for act in ACTIONS:
                    got_act, got_cls = await _inspect(det, act, enabled)
                    exp = act if enabled else "allow"
                    with self.subTest(detector=det, enabled=enabled, selected=act):
                        self.assertEqual(
                            got_act, exp,
                            f"{det} enabled={enabled} selected={act} -> got {got_act}")
                        if got_act != "allow":
                            ok = got_cls == CLASS[det] or (
                                det == "policy" and got_cls in ("policy", "policy_violation", "guard_model"))
                            self.assertTrue(ok, f"{det}: acted with wrong class {got_cls}")


class CrossClassIsolationTests(unittest.IsolatedAsyncioTestCase):
    """A detector's action governs ONLY its own class."""

    async def test_allowed_class_not_governed_by_other_block(self):
        # D=allow while every OTHER detector=block; feed only D's threat -> allow.
        for target in THREATS:
            cfg = _base_cfg()
            for d, (ek, ak) in FLAG.items():
                cfg[ek] = True
                cfg[ak] = "allow" if d == target else "block"
            sc = InputScanner()
            guard = OutputGuard(sc, cfg)
            ps = [patch.object(sc, "scan_output_with_tier2",
                               _policy_t2 if target == "policy"
                               else (lambda text, **kw: _clean_t2(sc, text, **kw)))]
            if target == "hallucination":
                async def _fs(*a, **k):
                    return HallucinationScore(risk_score=0.99, detail="x", matched_markers=["f"])
                ps.append(patch.object(guard, "score_hallucination", _fs))
            for p in ps:
                p.start()
            try:
                ctx = ["Paris is the capital of France."] if target == "hallucination" else None
                v = await guard.inspect(THREATS[target], context_chunks=ctx, org_config=cfg)
            finally:
                for p in ps:
                    p.stop()
            with self.subTest(target=target):
                self.assertEqual(v.action, "allow",
                                 f"{target}=allow was governed by another class's block")


class Finding1_IpFilePathRedact(unittest.TestCase):
    """ip_leakage=redact must mask a DETECTED file-path span (raw gone), not ship it
    verbatim in the 200 body because the maskable IP kept the redact->block floor
    from firing. The historical mask-ALL path stays FP-safe (path left raw)."""

    T = "Internal host db01.corp.local at 10.1.2.3 and file /home/svc/.ssh/id_rsa"

    def test_scoped_ip_redact_masks_file_path(self):
        out = redact_all_scoped(self.T, {"ip_leakage"})
        self.assertNotIn("/home/svc/.ssh/id_rsa", out)
        self.assertNotIn("10.1.2.3", out)

    def test_mask_all_leaves_file_path_raw_unchanged(self):
        # historical FP-avoidance for mask-all callers preserved byte-for-byte
        self.assertIn("/home/svc/.ssh/id_rsa", redact_all(self.T))

    def test_pii_scope_does_not_touch_ip_file_path(self):
        self.assertIn("/home/svc/.ssh/id_rsa", redact_all_scoped(self.T, {"pii"}))


class Finding4_MaskingEvasionLabelRouting(unittest.IsolatedAsyncioTestCase):
    """An adversarial masking-DEFEAT tier-2 label (inflection/synonym) must obey the
    operator's block, not get swallowed to allow; a benign already-masked label
    still correctly drops to allow."""

    async def _action_for(self, label):
        sc = InputScanner()
        cfg = _base_cfg()
        for _d, (ek, ak) in FLAG.items():
            cfg[ek] = True
            cfg[ak] = "block"

        async def _t2(text, **kw):
            return ScanVerdict(threat_type=label, action="block", confidence=0.9,
                               detail="x", matched_patterns=[label])
        guard = OutputGuard(sc, cfg)
        with patch.object(sc, "scan_output_with_tier2", _t2):
            v = await guard.inspect("some output", org_config=cfg)
        return v.action

    async def test_adversarial_masking_labels_are_blocked(self):
        for label in ("masking_bypassed", "masking_circumvention", "masking_evaded",
                      "masking_defeat", "masking_removed", "masking_stripped",
                      "masked_data_leak", "masking_disabled", "masked_exfiltration",
                      "masking_failure", "masking_bypass", "unmasking_attempt"):
            with self.subTest(label=label):
                self.assertEqual(await self._action_for(label), "block",
                                 f"adversarial label {label} was swallowed, not blocked")

    async def test_benign_masking_detection_labels_allow(self):
        for label in ("pii_masking_detection", "masking_detected", "output_masked"):
            with self.subTest(label=label):
                self.assertEqual(await self._action_for(label), "allow",
                                 f"benign already-masked label {label} should be allow")


def _sse(t: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n"


async def _stream_body(text, cfg, chunk, buf=4096):
    sc = InputScanner()

    async def inner():
        for i in range(0, len(text), chunk):
            yield _sse(text[i:i + chunk])
        yield "data: [DONE]\n\n"
    sec = SecureStreamingResponse(inner_generator=inner(), scanner=sc,
                                  output_guard=OutputGuard(sc, cfg),
                                  buffer_max_bytes=buf, max_buffer_chunks=64,
                                  enforcement_mode="block")
    out = []
    async for ch in sec:
        if ch.startswith("data: ") and "[DONE]" not in ch and "output_blocked" not in ch:
            try:
                d = json.loads(ch[6:].strip())
            except ValueError:
                continue
            c = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
            if c:
                out.append(c)
    return "".join(out)


class Findings2and3_StreamingRedactBoundarySplit(unittest.IsolatedAsyncioTestCase):
    """Streaming redact must not split a token at a flush boundary and egress its
    de-prefixed tail raw. A multi-threat response (email + key + IP), all=redact,
    must mask every class at EVERY chunk size — parity with non-stream."""

    CFG = {
        "output_guard_enabled": True, "output_tier2_enabled": False,
        "output_pii_enabled": True, "output_pii_action": "redact",
        "output_credential_enabled": True, "output_credential_action": "redact",
        "output_ip_leakage_enabled": True, "output_ip_leakage_action": "redact",
        "output_policy_enabled": False, "output_policy_action": "allow",
        "factuality_check_enabled": False, "output_hallucination_action": "allow",
    }
    TEXT = ("Contact john.doe@acme.com now. Key sk-proj-AbCdEf0123456789AbCdEf0123456789"
            " here. Host 10.0.0.5 internal.")
    KEY = "sk-proj-AbCdEf0123456789AbCdEf0123456789"
    IP = "10.0.0.5"
    EMAIL = "john.doe@acme.com"

    async def test_no_raw_token_leaks_at_any_chunk_size(self):
        for buf in (1_000_000, 4096):
            for chunk in (5, 8, 10, 13, 18, 20, 22, 40):
                out = await _stream_body(self.TEXT, self.CFG, chunk, buf)
                with self.subTest(buf=buf, chunk=chunk):
                    self.assertNotIn(self.KEY, out, f"raw key leaked (buf={buf} chunk={chunk})")
                    self.assertNotIn(self.IP, out, f"raw IP leaked (buf={buf} chunk={chunk})")
                    self.assertNotIn(self.EMAIL, out, f"raw email leaked (buf={buf} chunk={chunk})")

    async def test_clean_stream_passes_through_intact(self):
        clean = "The quarterly report is ready. The team will review it tomorrow. All nominal."
        for chunk in (7, 20):
            out = await _stream_body(clean, self.CFG, chunk)
            with self.subTest(chunk=chunk):
                self.assertEqual(out, clean)


if __name__ == "__main__":
    unittest.main()
