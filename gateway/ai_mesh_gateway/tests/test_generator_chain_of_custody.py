"""Chain-of-custody verification in the RAG GeneratorStage.

Covers the previously-untested manifest-rejection path (coverage-v5 gap) and
pins the fail-closed fix: when an approved manifest is present, a document with
NO ``_doc_id`` cannot be proven ranker-approved and MUST be dropped (it was
silently trusted before — a fail-open chain-of-custody bypass).
"""
from __future__ import annotations

import hashlib

import pytest

from rag_pipeline.generator_stage import GeneratorStage
from rag_pipeline.contracts import GeneratorStageInput, DocumentManifest


def _doc(doc_id, content):
    d = {"content": content, "id": doc_id or "x"}
    if doc_id is not None:
        d["_doc_id"] = doc_id
        d["_content_hash"] = hashlib.sha256(content.encode()).hexdigest()
    return d


def _manifest(doc_id, content):
    return DocumentManifest(
        doc_id=doc_id,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        source_stage="retriever",
        approved_by=["retriever", "ranker"],
    )


def _gen():
    return GeneratorStage(None, None, config={"rag_context_binding_enabled": False,
                                              "canary_tokens_enabled": False})


async def _run(gen, docs, manifest):
    return await gen.execute(GeneratorStageInput(
        documents=docs, query_text="q", project_id="org1",
        policy={}, escalation_level=0, key_hash="", approved_manifest=manifest,
    ))


async def test_approved_doc_passes_integrity_verified():
    out = await _run(_gen(), [_doc("d1", "approved content")], [_manifest("d1", "approved content")])
    assert [d["content"] for d in out.safe_documents] == ["approved content"]
    assert out.context_integrity_verified is True


async def test_unapproved_doc_id_rejected():
    out = await _run(
        _gen(),
        [_doc("d1", "approved content"), _doc("rogue", "unapproved content")],
        [_manifest("d1", "approved content")],
    )
    contents = [d["content"] for d in out.safe_documents]
    assert "unapproved content" not in contents
    assert out.context_integrity_verified is False


async def test_tampered_content_rejected_by_hash():
    # doc_id is approved, but content differs from the manifest hash -> tampered.
    tampered = _doc("d1", "TAMPERED content")  # its _content_hash is of tampered text
    out = await _run(_gen(), [tampered], [_manifest("d1", "original approved content")])
    # hash mismatch -> dropped -> no docs -> block
    assert out.verdict.action == "block"
    assert out.verdict.threat_type == "context_integrity"
    assert out.context_integrity_verified is False


async def test_missing_doc_id_is_failclosed():
    # The fix: an injected doc with no _doc_id cannot be proven approved -> drop.
    out = await _run(
        _gen(),
        [_doc("d1", "approved content"), _doc(None, "INJECTED context")],
        [_manifest("d1", "approved content")],
    )
    contents = [d["content"] for d in out.safe_documents]
    assert contents == ["approved content"]
    assert not any("INJECTED" in c for c in contents)
    assert out.context_integrity_verified is False


async def test_all_docs_rejected_blocks():
    out = await _run(_gen(), [_doc(None, "INJECTED only")], [_manifest("d1", "approved content")])
    assert out.verdict.action == "block"
    assert out.verdict.threat_type == "context_integrity"


async def test_no_manifest_legacy_path_trusts_docs():
    # Legacy path (no manifest) is intentionally pass-through — pin it so the
    # fail-closed change did not alter it.
    gen = _gen()
    out = await gen.execute(GeneratorStageInput(
        documents=[_doc("d1", "legacy content")], query_text="q", project_id="org1",
        policy={}, escalation_level=0, key_hash="", approved_manifest=[],
    ))
    assert [d["content"] for d in out.safe_documents] == ["legacy content"]
