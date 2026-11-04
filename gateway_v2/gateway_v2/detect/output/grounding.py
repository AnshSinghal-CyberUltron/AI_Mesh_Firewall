"""Grounding / factuality detector (GW33 C8, requirement 3).

For a response on a request that carries retrieval context, emits a `hallucination` finding whose
`confidence` is the risk score and whose spans are the ungrounded sentences (original offsets).

* No context chunks: one `SKIPPED` finding (recorded, contributes nothing).
* Lexical mode (deterministic, in-process): a sentence is supported when enough of its content
  words occur in the context AND at least one word shingle matches; sentences with too few content
  words to judge count as supported. Risk is `1 - supported_weight / total_weight`.
* Model mode (D-27): requires a backend that advertises `Category.HALLUCINATION`
  (`HallucinationCapable`), else `UNAVAILABLE` WITHOUT calling `classify`. A backend that answers
  no hallucination-category result is `UNAVAILABLE`, never a clean `EXECUTED`. The detector has no
  provider import: the backend is injected and local.

Over-bound input, a missing backend, a timeout or any exception is ONE `UNAVAILABLE` finding.
Imports `domain`, `detect.base`/`guard.backend`, the GW09 decoder and the ruleset only.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    SpanMap,
    canonicalize,
)
from gateway_v2.detect.guard.backend import Budget, GuardBackend, HallucinationCapable
from gateway_v2.detect.output.common import executed, original_span, unavailable
from gateway_v2.detect.output.ruleset import (
    GROUNDING_RULESET,
    MIN_CONTENT_WORDS,
    MIN_WORD_CHARS,
    SENTENCE_SUPPORT,
    SHINGLE_SIZE,
    STOP_WORDS,
    ruleset_version_of,
)
from gateway_v2.domain import Category, Finding, FindingStatus, GroundingMode, Span

__all__ = ("GroundingDetector",)

_VERSION: Final = ruleset_version_of(GROUNDING_RULESET)
_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)
_SENTENCE: Final = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def _content_words(text: str) -> list[str]:
    return [
        w for w in (m.group().casefold() for m in _WORD.finditer(text))
        if len(w) >= MIN_WORD_CHARS and w not in STOP_WORDS
    ]


def _shingles(words: Sequence[str]) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + SHINGLE_SIZE]) for i in range(len(words) - SHINGLE_SIZE + 1)}


@dataclass(frozen=True, slots=True)
class GroundingDetector:
    """Frozen detector, built per request with the supplied context chunks (D-15)."""

    mode: GroundingMode = GroundingMode.LEXICAL
    context_chunks: tuple[str, ...] = ()
    threshold: float = 0.5
    max_input_chars: int = 0
    backend: GuardBackend | None = None
    guard_budget_s: float = 1.0
    rule_id: str = "output.grounding"
    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def detector_version(self) -> str:
        return _VERSION

    def _unavailable(self) -> Finding:
        return unavailable(self.rule_id, _VERSION, Category.HALLUCINATION)

    def _skipped(self) -> Finding:
        return Finding(
            self.rule_id, _VERSION, Category.HALLUCINATION, FindingStatus.SKIPPED, None, (), None,
        )

    # -- sync entry (lexical only; the Detector protocol is synchronous) ---------------------- #

    def detect(self, text: str, context: DetectorContext | None = None) -> tuple[Finding, ...]:
        try:
            if not self.context_chunks:
                return (self._skipped(),)
            if self.mode is not GroundingMode.LEXICAL:
                return (self._unavailable(),)  # model mode needs the async entry
            return self._lexical(text)
        except Exception:  # noqa: BLE001 - D-18
            return (self._unavailable(),)

    # -- async entry (both modes) ------------------------------------------------------------- #

    async def adetect(self, text: str) -> tuple[Finding, ...]:
        try:
            if not self.context_chunks:
                return (self._skipped(),)
            if self.mode is GroundingMode.LEXICAL:
                return self._lexical(text)
            return await self._model(text)
        except Exception:  # noqa: BLE001 - D-18: absent/over-budget/raising backend is data
            return (self._unavailable(),)

    # -- internals ---------------------------------------------------------------------------- #

    def _within_bound(self, text: str) -> bool:
        total = len(text) + sum(len(c) for c in self.context_chunks)
        return self.max_input_chars > 0 and total <= self.max_input_chars

    def _sentences(self, text: str) -> tuple[str, SpanMap, list[tuple[int, int]]]:
        canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
        spans = [
            (m.start(), m.end()) for m in _SENTENCE.finditer(canonical) if m.group().strip()
        ]
        return canonical, span_map, spans

    def _lexical(self, text: str) -> tuple[Finding, ...]:
        if not self._within_bound(text):
            return (self._unavailable(),)
        try:
            canonical, span_map, sentences = self._sentences(text)
            context = "\n".join(
                canonicalize(chunk, DEFAULT_BUDGET)[0] for chunk in self.context_chunks
            )
        except BudgetExceeded:
            return (self._unavailable(),)
        context_words = set(_content_words(context))
        context_shingles = _shingles(_content_words(context))
        total = supported = 0
        ungrounded: list[Span] = []
        for start, end in sentences:
            words = _content_words(canonical[start:end])
            if not words:
                continue
            total += len(words)
            if len(words) < MIN_CONTENT_WORDS:
                supported += len(words)
                continue
            known = sum(1 for w in words if w in context_words) / len(words)
            if known >= SENTENCE_SUPPORT and (_shingles(words) & context_shingles):
                supported += len(words)
            else:
                ungrounded.append(original_span(span_map, start, end))
        if total == 0 or not ungrounded:
            return ()
        risk = 1.0 - supported / total
        if risk < self.threshold:
            return ()
        return (executed(self.rule_id, _VERSION, Category.HALLUCINATION, tuple(ungrounded), risk),)

    async def _model(self, text: str) -> tuple[Finding, ...]:
        backend = self.backend
        if (
            backend is None
            or not isinstance(backend, HallucinationCapable)
            or Category.HALLUCINATION not in backend.supported_categories
        ):
            return (self._unavailable(),)  # never call classify without the capability
        if not self._within_bound(text):
            return (self._unavailable(),)
        canonical, span_map, sentences = self._sentences(text)
        context = "\n".join(self.context_chunks)
        windows = [f"claim: {canonical[s:e].strip()}\ncontext: {context}" for s, e in sentences]
        if not windows:
            return ()
        results = await backend.classify(windows, Budget(remaining_s=self.guard_budget_s))
        answered = [r for r in results if r.category == Category.HALLUCINATION.value]
        if not answered:
            return (self._unavailable(),)  # never answering hallucination is not a clean pass
        hot = [
            r for r in answered
            if r.score >= self.threshold and 0 <= r.window_index < len(windows)
        ]
        if not hot:
            return ()
        spans = tuple(original_span(span_map, *sentences[r.window_index]) for r in hot)
        risk = max(r.score for r in hot)
        return (executed(self.rule_id, _VERSION, Category.HALLUCINATION, spans, min(1.0, risk)),)
