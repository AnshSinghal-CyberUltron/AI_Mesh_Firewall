"""
Tests for ``rag_pipeline.grounding_guard.GroundingGuard.check_grounding``.

Covers:
    1. mode=off → action=allow, no embedder call
    2. answer empty → action=allow, no embedder call
    3. context empty + mode=warn → action=warn, telemetry emitted
    4. high similarity (>=threshold) → action=allow, grounded=True
    5. low similarity + mode=warn → action=warn, grounded=False
    6. low similarity + mode=block → action=block
    7. low similarity + mode=downgrade → action=downgrade
    8. circuit-open → fail-OPEN with action=allow + error="circuit_open"
    9. PII required → fail-CLOSED, raises PIIRedactionRequiredError
"""
from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock, patch

import pytest

from ai_mesh_gateway.rag_pipeline.bedrock_embedder import (
    BedrockCircuitOpenError,
    PIIRedactionRequiredError,
)
from ai_mesh_gateway.rag_pipeline.grounding_guard import (
    GroundingVerdict,
    GroundingGuard,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _vec(seed: float, n: int = 256) -> List[float]:
    """Build a deterministic 256-dim vector. Two same-seed vectors are identical
    (cos=1). Orthogonal pair: seed=1.0 vs seed=0.0 with flipped first index."""
    return [seed] * n


class _StubEmbedder:
    """Minimal embedder stub returning canned vectors per input string."""

    def __init__(self, mapping: dict[str, List[float]]) -> None:
        self._mapping = mapping
        self.calls: List[tuple[str, str, bool]] = []
        self.raise_circuit = False
        self.raise_pii = False

    async def embed(self, text: str, *, org_slug: str, assume_redacted: bool = False):
        self.calls.append((text, org_slug, assume_redacted))
        if self.raise_pii and not assume_redacted:
            raise PIIRedactionRequiredError("attestation required")
        if self.raise_circuit:
            raise BedrockCircuitOpenError("breaker open")
        return self._mapping.get(text, _vec(0.0))


@pytest.fixture()
def emit_spy():
    with patch(
        "ai_mesh_gateway.rag_pipeline.grounding_guard.emit_operational_event",
        new=AsyncMock(),
    ) as spy:
        yield spy


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mode_off_returns_allow_without_embedding(emit_spy):
    embedder = _StubEmbedder({})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="anything",
        context_chunks=["c1"],
        org_slug="acme",
        mode="off",
        threshold=0.8,
        assume_redacted=True,
    )

    assert isinstance(verdict, GroundingVerdict)
    assert verdict.action == "allow"
    assert embedder.calls == []
    emit_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_answer_is_allow_noop(emit_spy):
    embedder = _StubEmbedder({})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="   ",
        context_chunks=["c1"],
        org_slug="acme",
        mode="block",
        threshold=0.5,
        assume_redacted=True,
    )

    assert verdict.action == "allow"
    assert embedder.calls == []
    emit_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_context_warns_when_mode_active(emit_spy):
    embedder = _StubEmbedder({})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="answer text",
        context_chunks=[],
        org_slug="acme",
        mode="warn",
        threshold=0.7,
        assume_redacted=True,
    )

    assert verdict.action == "warn"
    assert verdict.grounded is False
    assert embedder.calls == []
    emit_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_high_similarity_returns_allow(emit_spy):
    # Identical vectors → cosine == 1.0
    embedder = _StubEmbedder({
        "ans": _vec(1.0),
        "ctx1": _vec(1.0),
    })
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="ans",
        context_chunks=["ctx1"],
        org_slug="acme",
        mode="block",
        threshold=0.8,
        assume_redacted=True,
    )

    assert verdict.action == "allow"
    assert verdict.grounded is True
    assert verdict.max_similarity == pytest.approx(1.0)
    # No telemetry on allow (we only emit on non-allow / errors)
    emit_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_similarity_warn_mode_emits_warn(emit_spy):
    # Orthogonal vectors → cosine == 0.0 (well below threshold 0.8)
    a = [1.0] + [0.0] * 255
    b = [0.0] + [1.0] + [0.0] * 254
    embedder = _StubEmbedder({"ans": a, "ctx1": b})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="ans",
        context_chunks=["ctx1"],
        org_slug="acme",
        mode="warn",
        threshold=0.8,
        assume_redacted=True,
    )

    assert verdict.action == "warn"
    assert verdict.grounded is False
    assert verdict.max_similarity == pytest.approx(0.0, abs=1e-6)
    emit_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_similarity_block_mode_blocks(emit_spy):
    a = [1.0] + [0.0] * 255
    b = [0.0] + [1.0] + [0.0] * 254
    embedder = _StubEmbedder({"ans": a, "ctx": b})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="ans",
        context_chunks=["ctx"],
        org_slug="acme",
        mode="block",
        threshold=0.8,
        assume_redacted=True,
    )

    assert verdict.action == "block"
    assert verdict.grounded is False
    emit_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_low_similarity_downgrade_mode(emit_spy):
    a = [1.0] + [0.0] * 255
    b = [0.0] + [1.0] + [0.0] * 254
    embedder = _StubEmbedder({"ans": a, "ctx": b})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="ans",
        context_chunks=["ctx"],
        org_slug="acme",
        mode="downgrade",
        threshold=0.8,
        assume_redacted=True,
    )

    assert verdict.action == "downgrade"
    assert verdict.grounded is False


@pytest.mark.asyncio
async def test_circuit_open_fails_open_with_allow(emit_spy):
    embedder = _StubEmbedder({"ans": _vec(1.0), "ctx": _vec(1.0)})
    embedder.raise_circuit = True
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    verdict = await guard.check_grounding(
        answer_text="ans",
        context_chunks=["ctx"],
        org_slug="acme",
        mode="block",
        threshold=0.9,
        assume_redacted=True,
    )

    assert verdict.action == "allow"
    assert verdict.error == "circuit_open"
    emit_spy.assert_awaited_once()


@pytest.mark.asyncio
async def test_pii_required_raises_and_emits_block(emit_spy):
    embedder = _StubEmbedder({"ans": _vec(1.0), "ctx": _vec(1.0)})
    embedder.raise_pii = True
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    with pytest.raises(PIIRedactionRequiredError):
        await guard.check_grounding(
            answer_text="ans",
            context_chunks=["ctx"],
            org_slug="acme",
            mode="block",
            threshold=0.9,
            assume_redacted=False,
        )
    emit_spy.assert_awaited_once()


# --------------------------------------------------------------------------- #
# score() — plug-in backend for OutputGuard algorithm selector                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_score_returns_max_cosine_in_unit_interval():
    embedder = _StubEmbedder({"ans": _vec(1.0), "high": _vec(1.0), "low": _vec(0.0)})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    val = await guard.score(
        answer_text="ans",
        context_chunks=["high", "low"],
        org_slug="acme",
        assume_redacted=True,
    )

    assert val is not None
    assert 0.99 <= val <= 1.0


@pytest.mark.asyncio
async def test_score_empty_answer_or_context_returns_none():
    embedder = _StubEmbedder({})
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    assert await guard.score(
        answer_text="",
        context_chunks=["ctx"],
        org_slug="acme",
        assume_redacted=True,
    ) is None
    assert await guard.score(
        answer_text="ans",
        context_chunks=[],
        org_slug="acme",
        assume_redacted=True,
    ) is None
    assert embedder.calls == []


@pytest.mark.asyncio
async def test_score_circuit_open_fails_open_with_none():
    embedder = _StubEmbedder({"ans": _vec(1.0), "ctx": _vec(1.0)})
    embedder.raise_circuit = True
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    val = await guard.score(
        answer_text="ans",
        context_chunks=["ctx"],
        org_slug="acme",
        assume_redacted=True,
    )
    assert val is None


@pytest.mark.asyncio
async def test_score_pii_required_raises_fail_closed():
    embedder = _StubEmbedder({"ans": _vec(1.0), "ctx": _vec(1.0)})
    embedder.raise_pii = True
    guard = GroundingGuard(embedder=embedder)  # type: ignore[arg-type]

    with pytest.raises(PIIRedactionRequiredError):
        await guard.score(
            answer_text="ans",
            context_chunks=["ctx"],
            org_slug="acme",
            assume_redacted=False,
        )
