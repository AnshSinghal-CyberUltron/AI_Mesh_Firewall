"""Policy-driven detection — Task 7.3: embeddings surface is policy-driven.

Feature: policy-driven-detection (no default rules).

Task 7.3 makes ``_scan_redact_embedding_inputs`` / ``_scan_redact_metadata`` (the
``/v1/embeddings`` + RAG-ingest input/metadata redaction) run ONLY when an enabled
policy targets the input — the default-on ``input_scan_enabled`` gate is removed as
a detection driver, mirroring the established chat-input model (tasks 2/3):

  * Tier-1 = enabled policies ONLY (via ``policy_engine.evaluate``); there is NO
    built-in default scan and NO default-on redaction.
  * Redaction happens ONLY when a matched enabled Rule's action is ``redact`` (its
    hints applied). A matched enabled ``block`` Rule whose value cannot be masked
    fails CLOSED so the raw value never reaches the provider.
  * Zero enabled policies (or an unresolvable enabled-policy set) ⇒ embeddings
    Passthrough (no redaction, inputs returned unchanged). (R1.2 / R6.2 / R6.5.)

This module is ISOLATED from ``test_policy_driven_detection.py`` (it only imports
the shared scaffold / representative prompt sets read-only) per the task
constraints. It patches ``main.POLICY_SYNC`` + ``main.INPUT_SCANNER`` and drives
the two production helpers directly.

Validates: Requirements 6.1, 6.3 (and R1.2 / R6.2 / R6.5 for the passthrough +
fail-toward-no-detection legs).
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from ai_mesh_gateway import main as gateway_main

# Read-only reuse of the shared scaffold + representative prompt sets from the
# task-1 baseline module (NOT edited here, per the task constraints).
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    PolicyDrivenDetectionScaffold,
    PII_PROMPTS,
    ATTACK_PROMPTS,
    SECRET_PROMPTS,
    BENIGN_PROMPTS,
)


def _run(coro):
    return asyncio.run(coro)


class _FakePolicySync:
    """Minimal PolicySync double: ``is_loaded`` + per-org compiled bundle."""

    def __init__(self, policies_by_org: dict | None = None, *, loaded: bool = True):
        self._by_org = policies_by_org or {}
        self._loaded = loaded

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def get_policies(self, org_slug: str = "default"):
        return list(self._by_org.get(org_slug, []))


def _redact_email_policy(org_slug: str = "acme") -> dict:
    """One enabled pipeline-domain policy whose single rule REDACTS emails.

    ``policy_domain`` is omitted → normalizes to ``pipeline`` (the domain the
    embeddings surface resolves), so this entry targets the surface.
    """
    return {
        org_slug: [
            {
                "policy": {
                    "id": 1,
                    "code": "EMB_PKG_email",
                    "name": "Embedding Email Redaction",
                    "priority": 100,
                    "category": "pii",
                    "severity": "high",
                },
                "rules": [
                    {
                        "id": 11,
                        "name": "email-redact-rule",
                        "rule_type": "regex",
                        "condition": {
                            "regex": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                            "field": "prompt",
                        },
                        "action": "redact",
                    }
                ],
            }
        ]
    }


def _block_keyword_policy(org_slug: str = "acme", keyword: str = "topsecretxyz") -> dict:
    """One enabled pipeline-domain policy whose rule BLOCKS a keyword."""
    return {
        org_slug: [
            {
                "policy": {
                    "id": 2,
                    "code": "EMB_PKG_block",
                    "name": "Embedding Block Package",
                    "priority": 100,
                    "category": "test_family",
                    "severity": "high",
                },
                "rules": [
                    {
                        "id": 21,
                        "name": "kw-block-rule",
                        "rule_type": "keywords",
                        "condition": {"keywords": [keyword], "field": "prompt"},
                        "action": "block",
                    }
                ],
            }
        ]
    }


class EmbeddingsPolicyDrivenTests(PolicyDrivenDetectionScaffold, unittest.TestCase):
    """``_scan_redact_embedding_inputs`` redacts ONLY under an enabled policy."""

    def setUp(self):
        # The redaction path needs a non-None scanner sentinel (it no longer calls
        # scanner.scan_prompt; the enabled-policy gate + policy engine drive it).
        self._scanner_patch = mock.patch.object(gateway_main, "INPUT_SCANNER", object())
        self._scanner_patch.start()
        self.addCleanup(self._scanner_patch.stop)
        self.org_config = self.make_org_config()

    def _patch_policy_sync(self, fake):
        p = mock.patch.object(gateway_main, "POLICY_SYNC", fake)
        p.start()
        self.addCleanup(p.stop)

    # -- Zero-policy state ⇒ passthrough (default-on gate removed) ------------ #

    def test_zero_policy_no_redaction_all_categories(self):
        """No enabled policy ⇒ every representative input passes through raw."""
        self._patch_policy_sync(_FakePolicySync({}))  # loaded, but no policies
        texts = list(PII_PROMPTS.values()) + list(SECRET_PROMPTS.values()) + list(
            ATTACK_PROMPTS.values()
        ) + list(BENIGN_PROMPTS.values())
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs(list(texts), self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)  # byte-identical passthrough

    def test_no_policy_sync_loaded_is_passthrough(self):
        """Unresolvable enabled-policy set (cache not loaded) ⇒ passthrough (R6.5)."""
        self._patch_policy_sync(_FakePolicySync({}, loaded=False))
        texts = [PII_PROMPTS["email"]]
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs(texts, self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)

    def test_none_policy_sync_is_passthrough(self):
        """POLICY_SYNC absent entirely ⇒ passthrough (fail toward no detection)."""
        self._patch_policy_sync(None)
        texts = [PII_PROMPTS["email"], SECRET_PROMPTS["aws_key"]]
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs(texts, self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)

    def test_no_org_slug_is_passthrough(self):
        """Missing org_slug ⇒ enabled set unresolvable ⇒ passthrough."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        texts = [PII_PROMPTS["email"]]
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs(texts, self.org_config, None)
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)

    def test_scanner_none_is_passthrough(self):
        """Scanner infra unavailable ⇒ passthrough regardless of policy."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        with mock.patch.object(gateway_main, "INPUT_SCANNER", None):
            texts = [PII_PROMPTS["email"]]
            redacted, block = _run(
                gateway_main._scan_redact_embedding_inputs(texts, self.org_config, "acme")
            )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)

    # -- Enabled policy targeting the surface ⇒ detection runs --------------- #

    def test_enabled_redact_policy_masks_matching_input(self):
        """An enabled redact policy masks the email; the value never rides raw."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        raw = PII_PROMPTS["email"]  # contains alice.jones@example.com
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs([raw], self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(len(redacted), 1)
        self.assertNotEqual(redacted[0], raw)
        self.assertNotIn("alice.jones@example.com", redacted[0])

    def test_enabled_policy_only_fires_on_matching_input(self):
        """With a redact policy enabled, a NON-matching (benign) input passes raw."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        benign = BENIGN_PROMPTS["math"]  # "what is 2 + 2?" — no email
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs([benign], self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, [benign])  # unchanged

    def test_enabled_policy_isolated_to_its_org(self):
        """A policy enabled for org A does not fire for org B (passthrough)."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy("acme")))
        raw = PII_PROMPTS["email"]
        # Org "other" has no enabled policy ⇒ passthrough even for a matching input.
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs([raw], self.org_config, "other")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, [raw])

    def test_enabled_block_policy_matching_input_never_rides_raw(self):
        """An enabled block rule on a keyword ⇒ input is masked or fails closed,
        never forwarded raw."""
        self._patch_policy_sync(
            _FakePolicySync(_block_keyword_policy(keyword="topsecretxyz"))
        )
        raw = "please embed topsecretxyz for me"
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs([raw], self.org_config, "acme")
        )
        if block is not None:
            self.assertTrue(block.get("blocked"))
            self.assertEqual(block.get("index"), 0)
            # the raw keyword was NOT appended to the (partial) redacted list
            self.assertNotIn(raw, redacted)
        else:
            # masked path — the raw input must not be forwarded verbatim
            self.assertNotEqual(redacted[0], raw)

    def test_block_policy_does_not_fire_on_nonmatching_input(self):
        """A block policy leaves a non-matching input as passthrough."""
        self._patch_policy_sync(
            _FakePolicySync(_block_keyword_policy(keyword="topsecretxyz"))
        )
        benign = BENIGN_PROMPTS["greeting"]
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs([benign], self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, [benign])

    def test_ruleless_enabled_bundle_is_passthrough(self):
        """A policy entry with no rules does not target the surface ⇒ passthrough."""
        bundle = {"acme": [{"policy": {"id": 9, "code": "EMPTY"}, "rules": []}]}
        self._patch_policy_sync(_FakePolicySync(bundle))
        texts = [PII_PROMPTS["email"]]
        redacted, block = _run(
            gateway_main._scan_redact_embedding_inputs(texts, self.org_config, "acme")
        )
        self.assertIsNone(block)
        self.assertEqual(redacted, texts)


class MetadataPolicyDrivenTests(PolicyDrivenDetectionScaffold, unittest.TestCase):
    """``_scan_redact_metadata`` redacts values ONLY under an enabled policy."""

    def setUp(self):
        self._scanner_patch = mock.patch.object(gateway_main, "INPUT_SCANNER", object())
        self._scanner_patch.start()
        self.addCleanup(self._scanner_patch.stop)
        self.org_config = self.make_org_config()

    def _patch_policy_sync(self, fake):
        p = mock.patch.object(gateway_main, "POLICY_SYNC", fake)
        p.start()
        self.addCleanup(p.stop)

    def test_zero_policy_metadata_passthrough(self):
        """No enabled policy ⇒ metadata values returned unchanged."""
        self._patch_policy_sync(_FakePolicySync({}))
        meta = {"author": PII_PROMPTS["email"], "nested": {"note": "hello"}}
        out = _run(gateway_main._scan_redact_metadata(meta, self.org_config, "acme"))
        self.assertEqual(out, meta)

    def test_enabled_policy_redacts_metadata_value(self):
        """An enabled redact policy masks a PII value nested in metadata."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        meta = {"author": "contact alice.jones@example.com", "kind": "doc"}
        out = _run(gateway_main._scan_redact_metadata(meta, self.org_config, "acme"))
        self.assertNotIn("alice.jones@example.com", out.get("author", ""))
        self.assertEqual(out.get("kind"), "doc")  # non-matching value untouched

    def test_metadata_none_org_slug_passthrough(self):
        """Unresolvable enabled set (no org_slug) ⇒ metadata unchanged."""
        self._patch_policy_sync(_FakePolicySync(_redact_email_policy()))
        meta = {"author": "contact alice.jones@example.com"}
        out = _run(gateway_main._scan_redact_metadata(meta, self.org_config, None))
        self.assertEqual(out, meta)


class GateRemovalTests(unittest.TestCase):
    """The default-on ``input_scan_enabled`` gate is gone as a detection driver."""

    def test_input_scan_enabled_true_does_not_force_detection(self):
        """A stale ``input_scan_enabled=True`` config does NOT cause redaction when
        no enabled policy targets the surface (gate removed as a driver)."""
        with mock.patch.object(gateway_main, "INPUT_SCANNER", object()), mock.patch.object(
            gateway_main, "POLICY_SYNC", _FakePolicySync({})
        ):
            cfg = {"input_scan_enabled": True}
            raw = PII_PROMPTS["email"]
            redacted, block = _run(
                gateway_main._scan_redact_embedding_inputs([raw], cfg, "acme")
            )
            self.assertIsNone(block)
            self.assertEqual(redacted, [raw])  # not redacted despite the stale flag

    def test_helper_signatures_accept_org_slug(self):
        """Both helpers accept the org_slug the callers now thread through."""
        import inspect

        emb_sig = inspect.signature(gateway_main._scan_redact_embedding_inputs)
        self.assertIn("org_slug", emb_sig.parameters)
        meta_sig = inspect.signature(gateway_main._scan_redact_metadata)
        self.assertIn("org_slug", meta_sig.parameters)


if __name__ == "__main__":
    unittest.main()
