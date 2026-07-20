"""Ingest/upsert content scan is FAIL-CLOSED: a document the ContextGuard BLOCKS
for ANY reason — including an UNSCANNABLE oversized/ReDoS doc
(threat_type='scan_budget_exceeded') — is dropped, not stored.

This is the write-side counterpart to the iter-12 egress fix: unlike the E11b
egress backstop (which filtered on a narrow threat_type set), the upsert path
drops on ``verdict.action == 'block'`` regardless of threat_type. Pinned so a
future refactor can't silently reintroduce a fail-open on the write path.
"""
from __future__ import annotations

import pytest

import main  # ensure the gateway main module is in sys.modules for the helper's resolver
import vector_routes


def _resolved_main():
    """Patch the SAME module object vector_routes will resolve at runtime.

    The gateway file is importable under TWO identities (``main`` and
    ``ai_mesh_gateway.main``) and ``vector_routes._gateway_main_module()`` prefers
    ``sys.modules["ai_mesh_gateway.main"]``. Patching the bare ``main`` module works
    only while that identity is absent, so any earlier test importing
    ``ai_mesh_gateway.main`` (e.g. test_pipeline_output_redact) silently made these
    patches invisible and this file failed ONLY when run after it. Resolve the same
    way the code under test does.
    """
    return vector_routes._gateway_main_module() or main


class _V:
    def __init__(self, action, threat_type=""):
        self.action = action
        self.threat_type = threat_type


class _BlockGuard:
    """Blocks every document (as an unscannable/oversized verdict would)."""

    def __init__(self, threat_type):
        self._tt = threat_type

    async def scan_single_document(self, text):
        return _V("block", self._tt)


@pytest.mark.parametrize("threat_type", ["scan_budget_exceeded", "indirect_injection", "credential"])
async def test_upsert_drops_any_blocked_doc(monkeypatch, threat_type):
    monkeypatch.setattr(_resolved_main(), "CONTEXT_GUARD", _BlockGuard(threat_type), raising=False)

    kept_ids, kept_texts, kept_metas, scan_results, all_blocked = (
        await vector_routes._scan_redact_upsert_documents(
            ["d1"], ["some poisoned or unscannable document text"], [{}], org_slug="acme",
        )
    )
    assert all_blocked is True
    assert kept_texts == [] and kept_ids == []
    assert scan_results[0]["action"] == "block"


async def test_upsert_keeps_allowed_doc(monkeypatch):
    class _AllowGuard:
        async def scan_single_document(self, text):
            return _V("allow")

    monkeypatch.setattr(_resolved_main(), "CONTEXT_GUARD", _AllowGuard(), raising=False)
    # Neutralize redaction helpers so the allowed doc passes through unchanged.
    monkeypatch.setattr(_resolved_main(), "_scan_redact_embedding_inputs", None, raising=False)
    monkeypatch.setattr(_resolved_main(), "_scan_redact_metadata", None, raising=False)

    kept_ids, kept_texts, kept_metas, scan_results, all_blocked = (
        await vector_routes._scan_redact_upsert_documents(
            ["d1"], ["a perfectly benign refund-policy document"], [{}], org_slug="acme",
        )
    )
    assert all_blocked is False
    assert kept_texts == ["a perfectly benign refund-policy document"]
    assert scan_results[0]["action"] == "allow"
