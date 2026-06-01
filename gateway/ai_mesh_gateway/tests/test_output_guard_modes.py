"""
Tests for ``OutputGuard`` grounding-mode dispatcher (migration 0022).

Covers:
    1. mode="lexical" → uses Jaccard, does NOT call grounding guard
    2. mode="semantic" → uses guard.score(...)
    3. mode="hybrid" → averages lexical + semantic
    4. mode="semantic" + guard returns None → falls back to lexical
    5. mode="semantic" + guard.score raises → fail-OPEN to lexical
    6. mode="semantic" + no guard attached → falls back to lexical
    7. Unknown mode string → falls back to lexical
"""
from __future__ import annotations

from typing import List
from unittest.mock import AsyncMock

import pytest

from ai_mesh_gateway.output_guard import OutputGuard


# --------------------------------------------------------------------------- #
# Stubs                                                                       #
# --------------------------------------------------------------------------- #
class _AllowVerdict:
    """Duck-typed scanner verdict (avoids importing scanner.py which uses
    py3.10+ ``X | None`` syntax incompatible with Python 3.9 CI)."""

    action = "allow"
    threat_type = ""
    confidence = 0.0
    detail = ""
    matched_patterns: list = []


class _StubScanner:
    """Minimal scanner stub: scan_output returns allow-verdict."""

    async def scan_output(self, text: str):
        return _AllowVerdict()


class _StubGroundingGuard:
    """Records calls and returns a canned score (or raises)."""

    def __init__(self, score_value: float | None = 0.95, raise_exc: Exception | None = None) -> None:
        self._score = score_value
        self._raise = raise_exc
        self.calls: List[dict] = []

    async def score(
        self,
        *,
        answer_text: str,
        context_chunks: List[str],
        org_slug: str = "",
        assume_redacted: bool = False,
    ) -> float | None:
        self.calls.append(
            {
                "answer_text": answer_text,
                "context_chunks": list(context_chunks),
                "org_slug": org_slug,
                "assume_redacted": assume_redacted,
            }
        )
        if self._raise is not None:
            raise self._raise
        return self._score


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def guard() -> OutputGuard:
    return OutputGuard(scanner=_StubScanner(), config={"hallucination_flag_enabled": True})


# Answer text shares NO meaningful tokens with the context → low lexical score
ANSWER_TEXT = "quantum entanglement spans interstellar voids"
CONTEXT_CHUNKS = ["totally unrelated content about gardening and rabbits"]


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mode_lexical_does_not_call_grounding_guard(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=1.0)
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="lexical", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    assert score == pytest.approx(lexical_expected)
    assert stub_gg.calls == []


@pytest.mark.asyncio
async def test_mode_semantic_uses_guard(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=0.92)
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="semantic", org_slug="acme"
    )

    assert score == pytest.approx(0.92)
    assert len(stub_gg.calls) == 1
    call = stub_gg.calls[0]
    assert call["answer_text"] == ANSWER_TEXT
    assert call["context_chunks"] == CONTEXT_CHUNKS
    assert call["org_slug"] == "acme"
    assert call["assume_redacted"] is True


@pytest.mark.asyncio
async def test_mode_hybrid_averages_lexical_and_semantic(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=0.80)
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="hybrid", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    expected = (lexical_expected + 0.80) / 2.0
    assert score == pytest.approx(expected)
    assert len(stub_gg.calls) == 1


@pytest.mark.asyncio
async def test_mode_semantic_none_falls_back_to_lexical(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=None)
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="semantic", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    assert score == pytest.approx(lexical_expected)


@pytest.mark.asyncio
async def test_mode_semantic_exception_fails_open_to_lexical(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(raise_exc=RuntimeError("bedrock down"))
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="semantic", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    assert score == pytest.approx(lexical_expected)


@pytest.mark.asyncio
async def test_mode_semantic_no_guard_attached_falls_back(guard: OutputGuard) -> None:
    # No set_grounding_guard called.
    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="semantic", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    assert score == pytest.approx(lexical_expected)


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_to_lexical(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=0.5)
    guard.set_grounding_guard(stub_gg)

    score = await guard._compute_grounding_score_with_mode(
        ANSWER_TEXT, CONTEXT_CHUNKS, mode="bogus", org_slug="acme"
    )

    lexical_expected = guard._compute_grounding_score(ANSWER_TEXT, CONTEXT_CHUNKS)
    assert score == pytest.approx(lexical_expected)
    assert stub_gg.calls == []


# --------------------------------------------------------------------------- #
# End-to-end via score_hallucination (org_config dispatch)                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_score_hallucination_passes_org_mode_to_dispatcher(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=1.0)
    guard.set_grounding_guard(stub_gg)

    result = await guard.score_hallucination(
        ANSWER_TEXT,
        CONTEXT_CHUNKS,
        org_config={"hallucination_grounding_mode": "semantic"},
        org_slug="acme",
    )

    assert "[semantic]" in result.detail
    assert len(stub_gg.calls) == 1
    assert result.grounding_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_score_hallucination_default_mode_is_lexical(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=0.0)
    guard.set_grounding_guard(stub_gg)

    result = await guard.score_hallucination(
        ANSWER_TEXT,
        CONTEXT_CHUNKS,
        org_config={},
        org_slug="acme",
    )

    assert "[lexical]" in result.detail
    assert stub_gg.calls == []


@pytest.mark.asyncio
async def test_score_hallucination_no_context_skips_grounding(guard: OutputGuard) -> None:
    stub_gg = _StubGroundingGuard(score_value=0.0)
    guard.set_grounding_guard(stub_gg)

    result = await guard.score_hallucination(
        ANSWER_TEXT,
        context_chunks=None,
        org_config={"hallucination_grounding_mode": "semantic"},
        org_slug="acme",
    )

    # No context → no grounding call regardless of mode
    assert stub_gg.calls == []
    assert result.grounding_score == pytest.approx(1.0)
