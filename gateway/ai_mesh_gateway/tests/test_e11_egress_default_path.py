"""E11: client-egress retrieved-context PII backstop on the DEFAULT /v1/rag/query path.

The generation-time backstop ``_redact_retrieved_pii`` lives in GeneratorStage,
which is gated by ``rag_generator_enabled`` (default OFF). On the default
guardrails-only / ranker-only query paths the generator never runs, so the
gateway's ``/v1/rag/query`` handler now re-applies the SAME gated helper at the
single client-egress choke point — over every returned document's ``content``
(and any returned context_chunks) — so scanner-missed PII (e.g. a bare phone)
cannot reach the client raw, while legitimate large integers in benign citations
are not over-redacted.

These tests exercise the egress redaction LOOP that the handler added (mirrored
verbatim here) applied to a documents list on a generator-OFF path, plus the
gated helper directly. They do not require a live gateway / vector DB.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_pipeline.generator_stage import (
    _redact_retrieved_pii as _egress_redact_pii,
    _redact_metadata_values as _egress_redact_meta,
)


def _apply_egress_redaction(documents, context_chunks=None):
    """Verbatim mirror of the /v1/rag/query client-egress redaction loop in main.py.

    Redacts each returned document's ``content`` and any per-document /
    top-level ``context_chunks`` using the GATED helper. Idempotent and
    no-op on benign content.
    """
    if isinstance(documents, list):
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            content = doc.get("content")
            if isinstance(content, str) and content:
                doc["content"] = _egress_redact_pii(content)
            meta = doc.get("metadata")
            if isinstance(meta, (dict, list)):
                doc["metadata"] = _egress_redact_meta(meta)
            doc_chunks = doc.get("context_chunks")
            if isinstance(doc_chunks, list):
                doc["context_chunks"] = [
                    _egress_redact_pii(c) if isinstance(c, str) and c else c
                    for c in doc_chunks
                ]
    redacted_chunks = None
    if isinstance(context_chunks, list):
        redacted_chunks = [
            _egress_redact_pii(c) if isinstance(c, str) and c else c
            for c in context_chunks
        ]
    return documents, redacted_chunks


# ── PII inputs (must be masked at egress on a generator-OFF path) ──
_SSN = "123-45-6789"
_EMAIL = "evance.maps@mail.com"
_PHONE = "8929554991"  # bare separatorless 10-digit phone the typed redactor skips


def test_egress_masks_ssn_email_bare_phone_in_returned_document():
    """Generator-OFF path: a retrieved doc carrying SSN + email + bare phone
    must be redacted before it reaches the client."""
    documents = [
        {
            "_doc_id": "d1",
            "content": (
                f"Customer record: SSN {_SSN}, contact {_EMAIL}, "
                f"call back on {_PHONE}."
            ),
        }
    ]

    redacted_docs, _ = _apply_egress_redaction(documents)
    out = redacted_docs[0]["content"]

    # Raw PII values must NOT survive egress.
    assert _SSN not in out, f"SSN leaked at egress: {out!r}"
    assert _EMAIL not in out, f"email leaked at egress: {out!r}"
    assert _PHONE not in out, f"bare phone leaked at egress: {out!r}"

    # Typed placeholders / masked phone shape are present.
    assert "[SSN]" in out
    assert "[EMAIL]" in out
    assert "***-***-4991" in out


def test_egress_masks_bare_phone_only_when_other_pii_cooccurs():
    """The bare-digit backstop is GATED: a bare phone is masked precisely
    because real PII (SSN/email) co-occurs in the same chunk."""
    documents = [{"content": f"SSN {_SSN}; reach me at {_PHONE}."}]
    redacted_docs, _ = _apply_egress_redaction(documents)
    out = redacted_docs[0]["content"]
    assert _PHONE not in out
    assert "***-***-4991" in out
    assert "[SSN]" in out


def test_egress_does_not_over_redact_benign_large_integers():
    """No over-redaction: a benign doc with a legit large order id + revenue
    figure (no PII) passes through UNCHANGED — the gated digit backstop does
    not fire on a clean chunk."""
    benign = "Order 84920175 shipped. Quarterly revenue 12500000 USD, up 8%."
    documents = [{"_doc_id": "ok", "content": benign}]

    redacted_docs, _ = _apply_egress_redaction(documents)
    out = redacted_docs[0]["content"]

    assert out == benign, f"benign content mutated at egress: {out!r}"
    assert "84920175" in out
    assert "12500000" in out


def test_egress_redacts_context_chunks_list():
    """Any returned context_chunks list is redacted the same way as documents."""
    chunks = [
        f"SSN {_SSN} on file; phone {_PHONE}.",
        "Order 84920175 revenue 12500000.",  # benign — unchanged
    ]
    _, redacted_chunks = _apply_egress_redaction([], context_chunks=chunks)

    assert _SSN not in redacted_chunks[0]
    assert _PHONE not in redacted_chunks[0]
    assert "[SSN]" in redacted_chunks[0]
    assert "***-***-4991" in redacted_chunks[0]
    # Benign chunk untouched.
    assert redacted_chunks[1] == "Order 84920175 revenue 12500000."


def test_egress_redaction_is_idempotent():
    """Re-running egress redaction over already-redacted content is a no-op
    (safe when the generator already redacted on the generator-ON path)."""
    documents = [
        {"content": f"SSN {_SSN}, email {_EMAIL}, phone {_PHONE}."}
    ]
    once, _ = _apply_egress_redaction(documents)
    first = once[0]["content"]
    twice, _ = _apply_egress_redaction(once)
    assert twice[0]["content"] == first


def test_egress_handles_non_dict_and_missing_content_gracefully():
    """Malformed document entries (non-dict, no content) are skipped without error."""
    documents = ["not-a-dict", {"_doc_id": "x"}, {"content": ""}, {"content": None}]
    redacted_docs, _ = _apply_egress_redaction(documents)
    # No exception; entries preserved.
    assert redacted_docs[0] == "not-a-dict"
    assert redacted_docs[1] == {"_doc_id": "x"}


def test_egress_masks_pii_in_metadata_values():
    """Fail-open fix (HIGH, caught by adversarial verify): PII hidden in UNDECLARED
    metadata fields must be masked at egress — content-only redaction leaked it.
    Covers nested dict + list metadata values; benign metadata is preserved."""
    documents = [{
        "content": "Customer record on file.",
        "metadata": {
            "author": f"ssn {_SSN}",
            "note": f"reach me at {_EMAIL}",
            "nested": {"hint": f"call {_PHONE} or email {_EMAIL}"},
            "tags": [f"contact {_EMAIL}", "priority"],
            "order_id": "84920175",   # benign string — must survive
            "count": 42,              # non-string — untouched
        },
    }]
    redacted_docs, _ = _apply_egress_redaction(documents)
    meta = redacted_docs[0]["metadata"]
    blob = json.dumps(meta)

    assert _SSN not in blob, f"SSN leaked in metadata: {blob!r}"
    assert _EMAIL not in blob, f"email leaked in metadata: {blob!r}"
    assert _PHONE not in blob, f"bare phone leaked in metadata: {blob!r}"
    # Benign metadata preserved (no over-redaction of legit ids / non-strings).
    assert meta["order_id"] == "84920175"
    assert meta["count"] == 42
