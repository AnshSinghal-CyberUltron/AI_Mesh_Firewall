"""GW33 task 3.4: the untrusted local rewrite proposer (accept only re-inspected ALLOW text)."""
from __future__ import annotations

import ast
import asyncio
from collections.abc import Sequence
from pathlib import Path

from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    Transformation,
)
from gateway_v2.egress import rewrite_model
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.rewrite_model import RefineStats, refine_rewrites

_TEXT = "see 10.0.0.1 and 10.0.0.2 now"


def _decision() -> Decision:
    edits = (
        Transformation("rewrite", Span(4, 12), "[ip]"),
        Transformation("rewrite", Span(17, 25), "[ip]"),
    )
    finding = Finding("d", "v", Category.IP_LEAK, FindingStatus.EXECUTED, 1.0, (), None)
    return Decision(
        Disposition.REWRITE, (FindingDisposition("d", Disposition.REWRITE, 0),), edits,
        (finding,), "v", ("r",), (),
    )


class _Proposer:
    def __init__(self, answers: list[str | None | Exception]) -> None:
        self._answers = answers

    async def propose(self, original: str, template: str, max_chars: int) -> str | None:
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _clean(_: str) -> Sequence[Finding]:
    return ()


def _dirty(_: str) -> Sequence[Finding]:
    return (Finding("x", "v", Category.PII, FindingStatus.EXECUTED, 1.0, (Span(0, 1),), None),)


def _run(proposer: _Proposer | None, reinspect: object, max_chars: int = 40) -> tuple[Decision, list[RefineStats]]:
    seen: list[RefineStats] = []
    decision = asyncio.run(
        refine_rewrites(
            _TEXT, _decision(), proposer, reinspect,  # type: ignore[arg-type]
            MinimalOutputResolver(), max_chars, on_stats=seen.append,
        ),
    )
    return decision, seen


def test_accepted_proposal_swaps_replacement_only() -> None:
    decision, stats = _run(_Proposer(["an internal host", "another host"]), _clean)
    assert [t.replacement for t in decision.transformations] == ["an internal host", "another host"]
    assert [(t.kind, t.span) for t in decision.transformations] == [
        ("rewrite", Span(4, 12)), ("rewrite", Span(17, 25)),
    ]
    assert stats == [RefineStats(applied=2, fallback_template=0)]


def test_every_miss_keeps_the_template() -> None:
    for bad in (None, "", "x" * 41, RuntimeError("boom"), asyncio.TimeoutError()):
        decision, stats = _run(_Proposer([bad, bad]), _clean)
        assert [t.replacement for t in decision.transformations] == ["[ip]", "[ip]"]
        assert stats == [RefineStats(applied=0, fallback_template=2)]
    decision, _ = _run(_Proposer(["fine text", "other"]), _dirty)  # re-inspection is not ALLOW
    assert [t.replacement for t in decision.transformations] == ["[ip]", "[ip]"]


def test_absent_proposer_or_no_rewrite_edits_returns_the_decision_unchanged() -> None:
    original = _decision()
    assert _run(None, _clean)[0] == original
    redact = Decision(
        Disposition.REDACT, (FindingDisposition("d", Disposition.REDACT, 0),),
        (Transformation("redact", Span(0, 3), "[R]"),), (), "v", (), (),
    )
    out = asyncio.run(
        refine_rewrites(_TEXT, redact, _Proposer([]), _clean, MinimalOutputResolver(), 10),
    )
    assert out is redact


def test_refine_rewrites_never_builds_or_retargets_a_transformation() -> None:
    """Rule B stays neither tripped nor blinded: only ``replace(item, replacement=...)``."""
    tree = ast.parse(Path(rewrite_model.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "refine_rewrites")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            callee = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            assert callee != "Transformation"
            if callee == "replace":
                assert {kw.arg for kw in node.keywords} <= {"replacement", "transformations"}
