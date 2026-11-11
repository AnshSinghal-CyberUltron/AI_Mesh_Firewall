"""A customer keyword must not leave a streaming chat in pieces."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
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
from gateway_v2.egress.holdback_tradeoff import unsplittable_hold
from gateway_v2.egress.output_guard import REDACTION_PLACEHOLDER
from gateway_v2.egress.stream import DownstreamFrame, StreamPipeline, UpstreamChunk
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics

_TOKEN = "ZsCardToken99"


def test_cut_inside_a_match_moves_back_to_the_span_start() -> None:
    hits = (
        Finding(
            detector="kw",
            detector_version="t",
            category=Category.EXFIL,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=4, end=16),),
            evidence=None,
        ),
    )
    assert unsplittable_hold(hits, 0, 10, final=False) == 4
    assert unsplittable_hold(hits, 0, 10, final=True) == 10
    assert unsplittable_hold(hits, 0, 20, final=False) == 20


class _Redact:
    def decide(self, matches: Sequence[Finding]) -> Decision:
        if not matches:
            return Decision(
                disposition=Disposition.ALLOW,
                per_finding=(),
                transformations=(),
                findings=(),
                plan_version="gw26-stream",
                deciding_rules=(),
                unavailable_detectors=(),
            )
        spans = tuple(span for finding in matches for span in finding.spans)
        return Decision(
            disposition=Disposition.REDACT,
            per_finding=(FindingDisposition(detector="kw", disposition=Disposition.REDACT),),
            transformations=tuple(
                Transformation(kind="redact", span=span, replacement=REDACTION_PLACEHOLDER)
                for span in spans
            ),
            findings=tuple(matches),
            plan_version="gw26-stream",
            deciding_rules=("kw",),
            unavailable_detectors=(),
        )


def _keyword_detector(whole: str) -> list[Finding]:
    start = whole.find(_TOKEN)
    if start < 0:
        return []
    return [
        Finding(
            detector="kw",
            detector_version="t",
            category=Category.EXFIL,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=start, end=start + len(_TOKEN)),),
            evidence=None,
        )
    ]


def test_stream_redacts_a_keyword_the_card_hold_would_split() -> None:
    """``99`` looks like a held card tail. The keyword still leaves masked."""
    text = f"echo: quote {_TOKEN} please"
    words: list[str] = []
    current = ""
    for char in text:
        current += char
        if char.isspace():
            words.append(current)
            current = ""
    if current:
        words.append(current)
    chunks = [
        UpstreamChunk(text_deltas=(("content", word),), final=index == len(words) - 1)
        for index, word in enumerate(words)
    ]
    cfg, _logs = load_holdback_config({})
    pipe = StreamPipeline(
        scanner=holdback_scan,
        resolver=_Redact(),
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=HoldbackMetrics(),
        enforcing_output=True,
        window=max_pattern_length(),
        detector=_keyword_detector,
    )
    frames: list[DownstreamFrame] = []

    async def _send(frame: DownstreamFrame) -> None:
        frames.append(frame)

    async def _chunks():
        for chunk in chunks:
            yield chunk

    stats = asyncio.run(pipe.run(_chunks(), _send, lambda: False))
    body = "".join(delta for frame in frames for _channel, delta in frame.text_deltas)
    assert stats.error is None
    assert _TOKEN not in body
    assert REDACTION_PLACEHOLDER in body
    assert stats.output_action == "redact"
