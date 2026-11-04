"""GW33 task 7.5 / Property 13: grounding classification (lexical and capability-gated model)."""
from __future__ import annotations

import ast
import asyncio
import random
from collections.abc import Sequence
from pathlib import Path

from gateway_v2.detect.guard.backend import Budget, GuardResult
from gateway_v2.detect.output import grounding
from gateway_v2.detect.output.grounding import GroundingDetector
from gateway_v2.domain import Category, FindingStatus, GroundingMode
from gateway_v2.runtime.kinds import CapacityHint

_ITERATIONS = 600
_MAX = 1_000_000
_CONTEXT = (
    "The refund policy allows returns within thirty days of purchase. "
    "Shipping takes five business days for domestic orders.",
)
_GROUNDED = "The refund policy allows returns within thirty days of purchase."
_UNGROUNDED = "Purple elephants orchestrate quantum bakeries throughout Antarctica every Tuesday."
_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar "
    "papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu"
).split()


def _detector(**kw: object) -> GroundingDetector:
    return GroundingDetector(context_chunks=_CONTEXT, max_input_chars=_MAX, **kw)  # type: ignore[arg-type]


def test_no_context_is_skipped_never_a_fabricated_clean_result() -> None:
    findings = GroundingDetector(max_input_chars=_MAX).detect("anything at all")
    assert [f.status for f in findings] == [FindingStatus.SKIPPED]
    assert findings[0].confidence is None and findings[0].spans == ()


def test_property_13_risk_is_monotone_and_spans_cover_exactly_the_ungrounded_sentences() -> None:
    """Feature: gw33-output-parity, Property 13: Grounding classification. Validates 3.1-3.8."""
    # Feature: gw33-output-parity, Property 13: Grounding classification
    rng = random.Random(33_013)
    detector = _detector(threshold=0.0)
    for _ in range(_ITERATIONS):
        context = " ".join(
            " ".join(rng.choice(_WORDS) for _ in range(8)) + "." for _ in range(rng.randint(2, 5))
        )
        sentences = context.split(". ")
        supported = [s.rstrip(".") + "." for s in sentences]
        unsupported = [
            " ".join(rng.choice(["zzzz", "qqqq", "wwww", "vvvv"]) + str(rng.randint(0, 99))
                     for _ in range(8)) + "."
            for _ in range(rng.randint(0, 3))
        ]
        mixed = supported + unsupported
        rng.shuffle(mixed)
        d = GroundingDetector(context_chunks=(context,), max_input_chars=_MAX, threshold=0.0)
        text = " ".join(mixed)
        findings = d.detect(text)
        if not unsupported:
            assert findings == ()
            continue
        assert len(findings) == 1 and findings[0].status is FindingStatus.EXECUTED
        found = findings[0]
        assert found.category is Category.HALLUCINATION and found.evidence is None
        assert found.confidence is not None and 0.0 < found.confidence <= 1.0
        covered = sorted(text[s.start : s.end].strip() for s in found.spans)
        assert covered == sorted(unsupported)
        # Risk is monotone in the unsupported share: adding another unsupported sentence never lowers it.
        more = GroundingDetector(context_chunks=(context,), max_input_chars=_MAX, threshold=0.0)
        extra = more.detect(text + " " + unsupported[0])
        assert extra[0].confidence is not None and extra[0].confidence >= found.confidence - 1e-9
    assert detector.detect(_GROUNDED) == ()


def test_threshold_gates_the_finding() -> None:
    mixed = f"{_GROUNDED} {_UNGROUNDED}"
    assert _detector(threshold=0.0).detect(mixed)[0].status is FindingStatus.EXECUTED
    assert _detector(threshold=0.99).detect(mixed) == ()
    assert _detector(threshold=0.2).detect(_UNGROUNDED)[0].confidence == 1.0


def test_over_bound_input_is_unavailable() -> None:
    d = GroundingDetector(context_chunks=_CONTEXT, max_input_chars=10)
    assert [f.status for f in d.detect(_UNGROUNDED)] == [FindingStatus.UNAVAILABLE]
    assert [f.status for f in GroundingDetector(context_chunks=_CONTEXT).detect("x")] == [
        FindingStatus.UNAVAILABLE,
    ]  # an unset bound fails closed


class _Capable:
    """A stub backend that advertises hallucination and answers per window."""

    supported_categories = frozenset({Category.HALLUCINATION})

    def __init__(self, score: float, category: str = "hallucination", raises: bool = False) -> None:
        self.calls = 0
        self._score, self._category, self._raises = score, category, raises

    async def classify(self, windows: Sequence[str], budget: Budget) -> Sequence[GuardResult]:
        self.calls += 1
        if self._raises:
            raise RuntimeError("backend down")
        return [GuardResult(i, self._category, self._score) for i in range(len(windows))]

    async def readiness(self) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    def capacity_hint(self) -> CapacityHint:  # pragma: no cover - unused
        return CapacityHint(tokens_per_second=1.0)


class _Incapable(_Capable):
    supported_categories = frozenset({Category.PROMPT_INJECTION})


def _model(backend: object | None) -> GroundingDetector:
    return _detector(mode=GroundingMode.MODEL, backend=backend, threshold=0.5)  # type: ignore[arg-type]


def test_model_mode_success_path_with_a_capable_stub_backend() -> None:
    backend = _Capable(0.9)
    findings = asyncio.run(_model(backend).adetect(f"{_GROUNDED} {_UNGROUNDED}"))
    assert backend.calls == 1
    assert findings[0].status is FindingStatus.EXECUTED
    assert findings[0].confidence == 0.9 and len(findings[0].spans) == 2
    assert asyncio.run(_model(_Capable(0.1)).adetect(_UNGROUNDED)) == ()


def test_model_mode_is_unavailable_never_clean() -> None:
    incapable = _Incapable(0.9)
    cases = {
        "absent": _model(None),
        "does not advertise hallucination": _model(incapable),
        "answers no hallucination result": _model(_Capable(0.9, category="pii")),
        "raises": _model(_Capable(0.9, raises=True)),
    }
    for name, detector in cases.items():
        findings = asyncio.run(detector.adetect(_UNGROUNDED))
        assert [f.status for f in findings] == [FindingStatus.UNAVAILABLE], name
    assert incapable.calls == 0  # classify is never called without the capability
    sync = _model(_Capable(0.9)).detect(_UNGROUNDED)  # the sync entry cannot run model mode
    assert [f.status for f in sync] == [FindingStatus.UNAVAILABLE]


def test_the_detector_never_imports_a_provider_module() -> None:
    tree = ast.parse(Path(grounding.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    } | {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    forbidden = ("dispatch", "httpx", "openai", "requests", "urllib", "socket", "aiohttp")
    assert not [m for m in imported if any(part in m.split(".") for part in forbidden)]
