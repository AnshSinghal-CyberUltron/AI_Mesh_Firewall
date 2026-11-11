"""An output-side stream termination publishes ``block``, so the console does not say allow.

``output_blocked`` already published its decision. The holdback cuts
(``forced_release_tradeoff``, ``holdback_overflow``, ``undefined_tradeoff``) and
``scan_failure`` returned before publishing, so the edge saw no output action and
the console reported the stream as allowed and the model as never run.
"""

from __future__ import annotations

from gateway_v2.domain import Category, Disposition, Finding, FindingStatus, Span
from gateway_v2.egress.stream import (
    ScanResultLike,
    StreamStats,
    TokenCounterProtocol,
    UpstreamChunk,
)
from tests.egress.test_lgw12b_stream import (
    CHANNEL,
    _Collector,
    _drive,
    _for_plan,
    _force_release_everything,
    _KillAtBoundary,
    _literal_detector,
    _overflow_scanner,
    _plan,
    _rule,
    _run,
    _source,
)


def _stats(value: object) -> StreamStats:
    assert isinstance(value, StreamStats)
    return value


def test_forced_release_cut_publishes_its_block_decision() -> None:
    value = "eyJhbG.payload.sig"
    detector = _literal_detector(value, detector_class="jwt", category=Category.SECRET)
    _collector, raw = _drive(
        f"prefix {value} tail",
        scanner=_force_release_everything(held_class="jwt"),
        detector=detector,
    )
    stats = _stats(raw)
    assert stats.error == "forced_release_tradeoff"
    assert stats.output_action == "block"
    assert stats.output_decision is not None
    assert stats.output_decision.disposition is Disposition.BLOCK


def test_overflow_publishes_block_with_no_decision() -> None:
    _collector, raw = _drive(
        "aGVsbG8gd29ybGQ=", scanner=_overflow_scanner(held_class="base64"), detector=None
    )
    stats = _stats(raw)
    assert stats.error == "holdback_overflow"
    assert stats.output_action == "block"
    assert stats.output_decision is None


def test_undefined_tradeoff_publishes_block_with_no_decision() -> None:
    def _allow_detector(whole: str) -> list[Finding]:
        idx = whole.find("MARKER")
        if idx == -1:
            return []
        return [
            Finding(
                detector="unknown_class",
                detector_version="t",
                category=Category.SECRET,
                status=FindingStatus.SKIPPED,
                confidence=None,
                spans=(Span(idx, idx + len("MARKER")),),
                evidence=None,
            ),
        ]

    _collector, raw = _drive(
        "x MARKER y",
        scanner=_force_release_everything(held_class="unknown_class"),
        detector=_allow_detector,
    )
    stats = _stats(raw)
    assert stats.error == "undefined_tradeoff"
    assert stats.output_action == "block"
    assert stats.output_decision is None


def test_scan_failure_publishes_block_with_no_decision() -> None:
    def _raising_scanner(
        buf: str,
        *,
        final: bool,
        hold_cap_tokens: int,
        token_index: TokenCounterProtocol,
        window: int,
    ) -> ScanResultLike:
        raise RuntimeError("boom")

    _collector, raw = _drive("hello world", scanner=_raising_scanner, detector=None)
    stats = _stats(raw)
    assert stats.error == "scan_failure"
    assert stats.output_action == "block"
    assert stats.output_decision is None


def test_kill_cut_publishes_no_output_action() -> None:
    pipe = _for_plan(_plan(_rule("r1")))
    chunks = [
        UpstreamChunk(text_deltas=((CHANNEL, "hello world foo"),), final=False),
        UpstreamChunk(text_deltas=((CHANNEL, " more content here"),), final=True),
    ]
    stats = _stats(_run(pipe.run(_source(chunks), _Collector(), _KillAtBoundary(trip_on=2))))
    assert stats.error == "killed"
    assert stats.output_action is None
    assert stats.output_decision is None
