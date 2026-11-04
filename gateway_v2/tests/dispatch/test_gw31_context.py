"""GW31 context binding, merge_decisions (P15) and canary."""
from __future__ import annotations

import random

import pytest

from gateway_v2.dispatch.rag_context import (
    bind_chunks,
    canary_present,
    frame_context,
    merge_decisions,
    mint_canary,
)
from gateway_v2.domain.category import Category
from gateway_v2.domain.decision import (
    Decision,
    Disposition,
    FindingDisposition,
    Transformation,
)
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.rag import Chunk, ScoredChunk, TrustLevel
from gateway_v2.egress.output_guard import apply_decision


def _finding(start: int, end: int) -> Finding:
    return Finding("d", "1", Category.PII, FindingStatus.EXECUTED, 1.0, (Span(start, end),), None)


def _decision(spans: list[tuple[int, int]], disp: Disposition = Disposition.REDACT) -> Decision:
    return Decision(
        disp,
        tuple(FindingDisposition("d", disp, i) for i in range(len(spans))),
        tuple(Transformation("redact", Span(a, b), "X") for a, b in spans),
        tuple(_finding(a, b) for a, b in spans),
        "v1",
        ("r",),
        (),
    )


def test_bind_removes_foreign_chunks() -> None:
    mine = ScoredChunk(Chunk("a", "t", (1.0,), "o", "p", "s", TrustLevel.LOW), 1.0)
    other = ScoredChunk(Chunk("b", "t", (1.0,), "o", "q", "s", TrustLevel.LOW), 1.0)
    kept, removed = bind_chunks((mine, other), "o", "p")
    assert kept == (mine,) and removed == 1


def test_canary_tripwire_and_digest_only_frame() -> None:
    canary = mint_canary(lambda n: "c" * (2 * n), 4)
    frame = frame_context(("one", "two"), canary)
    assert canary in frame.text and canary not in frame.canary_digest
    assert canary_present(("clean", f"leak {canary}"), canary)
    assert not canary_present(("clean",), canary)
    assert not canary_present(("anything",), "")


def test_merge_none_cases() -> None:
    d = _decision([(0, 3)])
    assert merge_decisions(None, None) is None
    assert merge_decisions(d, None) is d and merge_decisions(None, d) is d


# Feature: gw31-rag-pipeline, Property 15: merged decision leaves no raw span and never overlaps
def test_property_merge_no_raw_survivor() -> None:
    rng = random.Random(15)
    for _ in range(10_000):
        text = "".join(rng.choice("abcdef") for _ in range(40))

        def spans() -> list[tuple[int, int]]:
            out: list[tuple[int, int]] = []
            cursor = 0
            for _ in range(rng.randint(0, 3)):
                if cursor >= 38:
                    break
                a = rng.randint(cursor, 38)
                b = rng.randint(a + 1, min(40, a + 8))
                out.append((a, b))
                cursor = b + 1
            return out

        s1, s2 = spans(), spans()
        d1 = _decision(s1) if s1 else None
        d2 = _decision(s2) if s2 else None
        merged = merge_decisions(d1, d2)
        if merged is None:
            continue
        out = apply_decision(text, merged, 0)
        covered = {i for a, b in s1 + s2 for i in range(a, b)}
        assert out.count("X") <= len(covered)
        kept_chars = [c for i, c in enumerate(text) if i not in covered]
        stripped = out.replace("X", "")
        assert stripped == "".join(kept_chars)


def test_merge_block_wins() -> None:
    merged = merge_decisions(_decision([(0, 2)]), _decision([(5, 7)], Disposition.BLOCK))
    assert merged is not None and merged.disposition is Disposition.BLOCK
    with pytest.raises(Exception):
        apply_decision("abcdefghij", merged, 0)
