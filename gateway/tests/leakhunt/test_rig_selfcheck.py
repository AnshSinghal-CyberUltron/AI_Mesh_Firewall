"""Self-check for the A2 egress rig: prove the rig itself is trustworthy.

A leak gate is only as good as its capture + assertion. These tests prove, end to
end against the REAL ``LLMRouter`` / ``InputScanner`` / ``main`` redaction paths
(only the upstream provider faked), that:

  1. the RecordingProvider captures the exact bytes each upstream call receives;
  2. ``assert_no_pii_egressed`` PASSES when the corpus's covered values are masked;
  3. ``assert_no_pii_egressed`` RAISES when a raw value is present (no no-op gate);
  4. the embedding egress path is captured the same way;
  5. ``RecordingVectorStore`` captures at-rest content + metadata.

If any of these regress, every downstream B-story gate is meaningless — so they
guard the gate, not the product.
"""
import pytest

import main
from llm_router import LLMRouter, _DEFAULT_EMBEDDING_MODEL
from scanner import InputScanner

import corpus
from recording_provider import (
    RecordingProvider,
    RecordingVectorStore,
    assert_no_pii_egressed,
    independent_pii_scan,
    serialize_wire,
)


def _router() -> LLMRouter:
    return LLMRouter({"org_only_inference": True})


@pytest.fixture
def provider(monkeypatch) -> RecordingProvider:
    return RecordingProvider().install(monkeypatch)


@pytest.mark.asyncio
async def test_rig_captures_chat_egress_bytes(provider):
    router = _router()
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "summarize the quarterly report"}],
    }
    status, _ = await router.acompletion(body, redacted_content=None)

    assert status == 200
    # The benign prompt reached the wire verbatim (capture is faithful).
    assert provider.chat, "no chat egress was captured"
    assert "summarize the quarterly report" in provider.wire_blob
    # No PII signal, no over-redaction, nothing flagged on the wire.
    provider.assert_no_pii_egressed(corpus.CORPUS)


@pytest.mark.asyncio
async def test_rig_passes_when_covered_pii_is_masked(provider):
    """Drive REAL redaction for the covered corpus values; the rig confirms they are
    gone from the wire."""
    scanner = InputScanner()
    covered = [
        c for c in corpus.REDACTABLE
        if c.label in {"email", "ssn"} or c.fmt == "bare10_contextual"
    ]
    prompt = "Please help. " + " ".join(c.prompt() for c in covered)
    verdict = await scanner.scan_prompt(prompt)
    redacted = scanner.redact_pii(prompt, verdict=verdict)

    router = _router()
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]}
    status, _ = await router.acompletion(body, redacted_content=redacted)

    assert status == 200
    # Every covered value is absent from the captured wire.
    provider.assert_no_pii_egressed(covered)


@pytest.mark.asyncio
async def test_assertion_actually_detects_a_leak(provider):
    """Negative control: if a raw value IS on the wire, the assertion MUST raise.

    Guards against a silently-passing (no-op) gate. We forge a wire by recording a
    deliberately-unredacted call, then assert the gate catches it."""
    leaky = corpus.by_label("email")[0]
    forged_wire = serialize_wire(
        {"model": "gpt-4o-mini",
         "messages": [{"role": "user", "content": leaky.prompt()}]}
    )
    assert leaky.raw in forged_wire
    assert "email" in independent_pii_scan(forged_wire)
    with pytest.raises(AssertionError):
        assert_no_pii_egressed([forged_wire], [leaky])


@pytest.mark.asyncio
async def test_rig_captures_embedding_egress_bytes(provider, monkeypatch):
    """Embedding redaction is upstream (main._scan_redact_embedding_inputs); the rig
    captures the masked input that reaches the embedding provider."""
    monkeypatch.setattr(main, "INPUT_SCANNER", InputScanner())

    raw_texts = ["my phone number is 4155550142", "contact evance.maps@mail.com please"]
    masked, block = await main._scan_redact_embedding_inputs(
        raw_texts, {"input_scan_enabled": True}
    )
    assert block is None

    router = _router()
    status, _ = await router.aembedding({"model": _DEFAULT_EMBEDDING_MODEL, "input": masked})

    assert status == 200
    assert provider.embed, "no embedding egress was captured"
    # Exactly the masked input reached litellm.aembedding (no re-stash of raw).
    assert provider.embed[0]["input"] == masked
    assert_no_pii_egressed(
        provider.wires, ["4155550142", "evance.maps@mail.com"]
    )


def test_recording_vector_store_captures_at_rest():
    store = RecordingVectorStore()
    store.add(
        ids=["d1"],
        documents=["clean document body"],
        metadatas=[{"source": "kb", "note": "no pii here"}],
    )
    assert store.documents == ["clean document body"]
    assert store.metadatas[0]["source"] == "kb"
    store.assert_no_pii_at_rest(corpus.CORPUS)

    # And it detects a metadata leak (the classic at-rest blind spot).
    leaky = corpus.by_label("email")[0]
    store.add(ids=["d2"], documents=["body"], metadatas=[{"owner": leaky.raw}])
    with pytest.raises(AssertionError):
        store.assert_no_pii_at_rest([leaky])
