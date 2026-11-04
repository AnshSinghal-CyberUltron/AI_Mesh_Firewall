"""GW31 context binding, decision merge, canary tripwire and the Grounder/Answerer seams.

Pure given an injected ``token_source``. No detector, no resolver, no HTTP. The frame stores only
``sha256(canary)``; the canary itself is never logged, audited or placed in an error body.

The canary is a tripwire for context-frame leakage, not a proof about model behaviour.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain.decision import (
    Decision,
    FindingDisposition,
    Transformation,
    most_restrictive,
)
from gateway_v2.domain.finding import Span
from gateway_v2.domain.rag import ScoredChunk

__all__ = (
    "Answerer",
    "ContextFrame",
    "Grounder",
    "GroundingVerdict",
    "bind_chunks",
    "canary_present",
    "frame_context",
    "merge_decisions",
    "mint_canary",
)

GROUND_OK = "ok"
GROUND_UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class GroundingVerdict:
    code: str
    score: float | None = None

    @property
    def available(self) -> bool:
        return self.code == GROUND_OK


class Grounder(Protocol):
    async def check(self, frame_text: str, query: str) -> GroundingVerdict: ...


class Answerer(Protocol):
    async def answer(self, frame_text: str, query: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ContextFrame:
    text: str
    canary_digest: str


def bind_chunks(
    chunks: tuple[ScoredChunk, ...], org_id: str, project: str
) -> tuple[tuple[ScoredChunk, ...], int]:
    """Keep chunks bound to this request's (org, project). Returns ``(kept, removed)``."""
    kept = tuple(c for c in chunks if c.chunk.org_id == org_id and c.chunk.project == project)
    return kept, len(chunks) - len(kept)


def mint_canary(token_source: Callable[[int], str], nbytes: int) -> str:
    return token_source(nbytes)


def frame_context(texts: tuple[str, ...], canary: str) -> ContextFrame:
    body = "\n".join(texts)
    digest = hashlib.sha256(canary.encode()).hexdigest()
    return ContextFrame(f"<<ctx:{canary}>>\n{body}\n<</ctx:{canary}>>", digest)


def canary_present(outgoing: tuple[str, ...], canary: str) -> bool:
    return bool(canary) and any(canary in text for text in outgoing)


def merge_decisions(first: Decision | None, second: Decision | None) -> Decision | None:
    """Combine a retriever and a ranker decision for one chunk.

    Dispositions combine with ``most_restrictive``. Overlapping or touching transformation spans
    are coalesced into their union carrying the replacement of the earliest-starting one (ties:
    ``first``), so no raw byte of either finding survives and no overlap reaches
    ``apply_decision``. ``second``'s ``finding_index`` values are offset by ``first``'s finding
    count.
    """
    if first is None or second is None:
        return first if second is None else second
    offset = len(first.findings)
    per_finding = first.per_finding + tuple(
        FindingDisposition(
            f.detector,
            f.disposition,
            None if f.finding_index is None else f.finding_index + offset,
        )
        for f in second.per_finding
    )
    disposition = most_restrictive(tuple(f.disposition for f in per_finding))
    if not per_finding:
        disposition = most_restrictive((first.disposition, second.disposition))
    rules = first.deciding_rules + tuple(
        r for r in second.deciding_rules if r not in first.deciding_rules
    )
    unavailable = first.unavailable_detectors + tuple(
        d for d in second.unavailable_detectors if d not in first.unavailable_detectors
    )
    return Decision(
        disposition=disposition,
        per_finding=per_finding,
        transformations=_coalesce(first.transformations + second.transformations),
        findings=first.findings + second.findings,
        plan_version=first.plan_version,
        deciding_rules=rules,
        unavailable_detectors=unavailable,
    )


def _coalesce(items: tuple[Transformation, ...]) -> tuple[Transformation, ...]:
    ordered = sorted(items, key=lambda t: (t.span.start, t.span.end))
    out: list[Transformation] = []
    for item in ordered:
        if out and item.span.start <= out[-1].span.end:
            last = out[-1]
            end = max(last.span.end, item.span.end)
            out[-1] = Transformation(last.kind, Span(last.span.start, end), last.replacement)
        else:
            out.append(item)
    return tuple(out)
