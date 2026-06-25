"""E11: generation-time retrieved-context PII redaction backstop.

Closes the recon-confirmed core-1.2 leak: a document ingested while
``rag_redaction_enabled`` was OFF (the default for existing orgs) stores raw
PII verbatim. At retrieval, the generator stage previously only ran
``redact_structured_fields`` (JSON-block fields by sensitivity) — PLAIN-TEXT
PII (SSN, email, bare phone) was forwarded to the generator/LLM, the
output-guard grounding context, and the client. There was no generation-time
backstop.

These tests prove the GeneratorStage now redacts plain-text PII in retrieved
chunks UNCONDITIONALLY — even when the org config has
``rag_redaction_enabled=False`` (toggle-off fail-safe) — and that benign
content passes through unchanged (no over-redaction).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from rag_pipeline.contracts import GeneratorStageInput
from rag_pipeline.generator_stage import GeneratorStage, _redact_retrieved_pii


# The exact PII fixtures from the recon report.
SSN = "123-45-6789"
EMAIL = "evance.maps@mail.com"
BARE_PHONE = "8929554991"

LEAKY_CONTENT = (
    f"Employee record. SSN {SSN}. Reach them at {EMAIL} "
    f"or by phone {BARE_PHONE} for callbacks."
)


def _make_stage(rag_redaction_enabled: bool) -> GeneratorStage:
    """GeneratorStage with NO leakage detector / redis / canary, so the only
    transform applied to chunk content is the redaction step under test.

    ``rag_redaction_enabled`` is threaded into the config exactly as it would be
    at runtime so we can prove the backstop fires even when it is False.
    """
    return GeneratorStage(
        leakage_detector=None,
        redis_client=None,
        config={
            "rag_redaction_enabled": rag_redaction_enabled,
            "canary_tokens_enabled": False,
            "rag_context_binding_enabled": False,
        },
        canary_token_manager=None,
    )


def _gen_input(content: str) -> GeneratorStageInput:
    # Legacy trust-all path: no approved_manifest, so the document is not
    # rejected and reaches the redaction loop. policy left default.
    return GeneratorStageInput(
        documents=[{"content": content}],
        query_text="who is the employee?",
        project_id="proj-e11",
        policy={},
        escalation_level=0,
        key_hash="hash-e11",
    )


# ───────────────────────── unit: the redaction step ─────────────────────────


def test_unit_plaintext_pii_redacted():
    """The helper masks SSN, email, AND bare phone in plain text."""
    out = _redact_retrieved_pii(LEAKY_CONTENT)
    assert SSN not in out
    assert EMAIL not in out
    assert BARE_PHONE not in out
    # Typed placeholders for SSN/email, ***-***-#### shape for the bare phone.
    assert "[SSN]" in out
    assert "[EMAIL]" in out
    assert "***-***-4991" in out


def test_unit_benign_content_unchanged():
    """No over-redaction: benign prose passes through byte-for-byte."""
    benign = "The quarterly report shows revenue grew 12 percent year over year."
    assert _redact_retrieved_pii(benign) == benign


def test_unit_clean_large_integers_not_mangled():
    """FP regression (HIGH, caught by adversarial verify): legitimate 7+-digit
    numbers in a PII-FREE chunk must pass through BYTE-FOR-BYTE. The digit
    backstop runs ONLY when the typed redactor already found PII, so order IDs /
    revenue figures / epochs / build numbers are never mangled (F12 over-block)."""
    benign = (
        "Order 84920175 shipped. Q3 revenue 12500000 dollars. "
        "Epoch 1718900000, build 20240613, ref 55500000."
    )
    assert _redact_retrieved_pii(benign) == benign


def test_unit_bare_phone_masked_when_pii_co_occurs():
    """Mixed PII: the bare phone co-occurs with an email, so the typed redactor
    fires (redacted != content) and the digit backstop masks the phone."""
    out = _redact_retrieved_pii(f"reach {EMAIL} or {BARE_PHONE} today")
    assert EMAIL not in out
    assert BARE_PHONE not in out
    assert "***-***-4991" in out


def test_unit_bare_phone_alone_is_accepted_residual():
    """Documented trade-off: a bare 10-digit run with NO other PII is
    indistinguishable from an order ID, so the digit backstop is NOT applied here
    (avoids the F12 over-block). Contextual bare phones are caught upstream by
    patterns.phone_us_bare_contextual; this asserts the deliberate FN at this layer."""
    assert _redact_retrieved_pii(BARE_PHONE) == BARE_PHONE


def test_unit_empty_and_nonstr_passthrough():
    assert _redact_retrieved_pii("") == ""
    assert _redact_retrieved_pii(None) is None  # type: ignore[arg-type]


# ───────────── integration: drive GeneratorStage.execute() ─────────────


@pytest.mark.asyncio
async def test_generator_redacts_plaintext_pii_when_toggle_off():
    """GATE fail-safe: even with rag_redaction_enabled=False, the retrieved
    chunk's plain-text PII is redacted in context_chunks (what reaches the LLM,
    the output-guard grounding, and the client)."""
    stage = _make_stage(rag_redaction_enabled=False)
    out = await stage.execute(_gen_input(LEAKY_CONTENT))

    assert out.verdict.action == "allow"
    assert out.context_chunks, "expected a non-empty context chunk"
    joined = "\n".join(out.context_chunks)

    # No raw PII survives to the egress-bound context.
    assert SSN not in joined
    assert EMAIL not in joined
    assert BARE_PHONE not in joined
    assert "[SSN]" in joined
    assert "[EMAIL]" in joined
    assert "***-***-4991" in joined

    # The sanitized content is written back onto the document itself too, so the
    # safe_documents forwarded downstream carry no raw PII.
    assert out.safe_documents
    safe_text = out.safe_documents[0].get("content", "")
    assert SSN not in safe_text
    assert EMAIL not in safe_text
    assert BARE_PHONE not in safe_text


@pytest.mark.asyncio
async def test_generator_redacts_plaintext_pii_when_toggle_on():
    """Symmetry: the backstop also fires when redaction is enabled."""
    stage = _make_stage(rag_redaction_enabled=True)
    out = await stage.execute(_gen_input(LEAKY_CONTENT))
    joined = "\n".join(out.context_chunks)
    assert SSN not in joined and EMAIL not in joined and BARE_PHONE not in joined


@pytest.mark.asyncio
async def test_generator_benign_chunk_unchanged():
    """No over-redaction through the full stage: benign content is verbatim."""
    benign = "Onboarding guide: submit your timesheet every Friday by 5pm."
    stage = _make_stage(rag_redaction_enabled=False)
    out = await stage.execute(_gen_input(benign))
    assert out.context_chunks == [benign]
