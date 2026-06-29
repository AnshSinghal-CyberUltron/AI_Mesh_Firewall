"""B3 — scan + redact /v1/embeddings input (G1): egress-truth on the embedding path.

PRIME INVARIANT (progress.txt): *egress = truth*. Unlike the chat path, embedding
inputs were historically NEVER scanned or redacted before they were dispatched to
the upstream embedding provider — so a customer email / SSN / phone / API key was
embedded verbatim by a third party (G1). B3 closes that gap with the SAME tier-1
``InputScanner`` + verdict-aware redactor + fail-closed digit backstop the chat path
uses (``main._scan_redact_embedding_inputs``), gated by the SAME
``input_scan_enabled`` config so there is no behavior change when scanning is off.

These tests drive the REAL embedding egress path end to end:

    main._scan_redact_embedding_inputs(texts)  ->  LLM_ROUTER.aembedding(body)

with ONLY the upstream provider faked (``RecordingProvider`` patches the
module-level ``litellm.aembedding`` — the choke point when ``org_only_inference``
empties the router). We then assert against the *captured wire bytes* — not the
pipeline trace or the client response — that no raw corpus value reached the
provider. The independent oracle in ``recording_provider`` cross-checks so a leak is
never missed merely because the production regex that should have caught it is the
same one that failed.
"""
import sys
from pathlib import Path

import pytest

# main lives in the gateway package dir (on sys.path via conftest); import it so we
# can install the real scanner singleton + call the real redaction helper.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai_mesh_gateway"))

import main  # noqa: E402
from scanner import InputScanner, ScanVerdict  # noqa: E402
from llm_router import LLMRouter  # noqa: E402

from corpus import REDACTABLE, by_label  # noqa: E402
from recording_provider import RecordingProvider, independent_pii_scan  # noqa: E402

_ORG_CONFIG = {"input_scan_enabled": True}
# text-embedding-3-small is _DEFAULT_EMBEDDING_MODEL → always in the allowed set,
# so an empty (org_only_inference) router dispatches to litellm.aembedding.
_EMBED_MODEL = "text-embedding-3-small"


@pytest.fixture
def real_scanner(monkeypatch):
    """Install the real tier-1 InputScanner as the module singleton (no network)."""
    scanner = InputScanner()
    monkeypatch.setattr(main, "INPUT_SCANNER", scanner)
    return scanner


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


async def _embed_through_gateway(texts, provider):
    """Run the FULL embedding egress path: scan+redact, then dispatch, returning the
    block-meta (or None). Egress bytes are captured by ``provider``."""
    redacted, block = await main._scan_redact_embedding_inputs(texts, _ORG_CONFIG)
    if block is not None:
        # Fail-closed: the handler returns a 403 and NEVER dispatches. Mirror that —
        # nothing must reach the wire.
        return block
    body = {"model": _EMBED_MODEL, "input": redacted}
    status, _ = await _router().aembedding(body)
    assert status == 200, f"embedding dispatch failed: {status}"
    return None


# ── Egress-truth: every redactable corpus value is absent from the embedding wire ──
@pytest.mark.parametrize("item", REDACTABLE, ids=lambda it: f"{it.label}:{it.fmt}")
@pytest.mark.asyncio
async def test_b3_redactable_corpus_never_egresses_to_embedding_provider(
    item, real_scanner, monkeypatch
):
    provider = RecordingProvider().install(monkeypatch)
    block = await _embed_through_gateway([item.prompt()], provider)
    assert block is None, f"{item.fmt} unexpectedly blocked: {block}"
    assert provider.embed, "no embedding call was captured — egress path not exercised"
    assert item.raw not in provider.wire_blob, (
        f"raw {item.label}/{item.fmt} {item.raw!r} reached the embedding provider "
        f"(egress != verdict); oracle classes on wire: "
        f"{independent_pii_scan(provider.wire_blob)!r}"
    )


# ── Batch parity: a mixed batch redacts EACH item; clean items pass through ──
@pytest.mark.asyncio
async def test_b3_batch_redacts_each_and_keeps_clean(real_scanner, monkeypatch):
    provider = RecordingProvider().install(monkeypatch)
    email = by_label("email")[0]
    ssn = by_label("ssn")[0]
    clean = "the quick brown fox jumps over the lazy dog"
    block = await _embed_through_gateway([clean, email.prompt(), ssn.prompt()], provider)
    assert block is None
    wire = provider.wire_blob
    assert email.raw not in wire
    assert ssn.raw not in wire
    # The clean document must survive verbatim — no over-redaction of benign text.
    assert clean in wire


# ── Fail-closed (G4): detected PII that genuinely cannot be masked → NO egress ──
class _UnmaskableScanner:
    """Detects PII but redaction is a no-op (masking impossible) — drives the
    byte-verified fail-closed branch so a raw value cannot ride to the provider."""

    async def scan_prompt(self, text, *a, **k):
        return ScanVerdict(
            action="redact",
            threat_type="pii",
            confidence=0.85,
            detail="PII detected",
            matched_patterns=["full_name"],
            tier="tier_1",
        )

    def redact_pii(self, text, verdict=None):
        return text  # cannot mask


@pytest.mark.asyncio
async def test_b3_fail_closed_unmaskable_never_egresses(monkeypatch):
    monkeypatch.setattr(main, "INPUT_SCANNER", _UnmaskableScanner())
    provider = RecordingProvider().install(monkeypatch)
    raw = "John Q Public is the account holder"
    block = await _embed_through_gateway([raw], provider)
    assert block is not None and block["blocked"] is True
    # The defining guarantee: nothing was dispatched, so the raw value never left.
    assert provider.embed == [], "fail-closed input must NOT reach the embedding provider"
    assert raw not in provider.wire_blob


# ── No-regression: scanning disabled → unchanged input, but no phantom redaction claim ──
@pytest.mark.asyncio
async def test_b3_scan_disabled_forwards_unchanged(real_scanner, monkeypatch):
    provider = RecordingProvider().install(monkeypatch)
    email = by_label("email")[0]
    redacted, block = await main._scan_redact_embedding_inputs(
        [email.prompt()], {"input_scan_enabled": False}
    )
    assert block is None
    status, _ = await _router().aembedding({"model": _EMBED_MODEL, "input": redacted})
    assert status == 200
    # Honest behavior: with scanning OFF the value is forwarded raw (no claim it was
    # redacted). The OPT-IN config is the only knob — egress matches the verdict.
    assert email.raw in provider.wire_blob
