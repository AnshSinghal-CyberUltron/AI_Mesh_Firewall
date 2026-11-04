"""GW34 Task 6: meta-channel pass-through and per-call isolation in both enforcing pipelines."""
from __future__ import annotations

import random
import time

from gateway_v2.domain import Category
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import StreamPipeline, UpstreamChunk
from gateway_v2.egress.strict import StrictWithholdPipeline
from gateway_v2.runtime.strict_metrics import StrictMetrics
from tests.egress.test_lgw12b_stream import (
    _Collector,
    _literal_detector,
    _never_killed,
    _plan,
    _rule,
    _run,
    _source,
)

_SECRET = "AKIAIOSFODNN7EXAMPLE"
_ARGS0 = "output_tool_arguments#0"
_ARGS1 = "output_tool_arguments#1"


def _incremental(detector: object) -> object:
    return StreamPipeline.for_plan(
        _plan(_rule("r1")),
        **{**_for_plan_kwargs(), "detector": detector},  # type: ignore[arg-type]
    )


def _for_plan_kwargs() -> dict[str, object]:
    from gateway_v2.detect.holdback import TokenIndex, scan
    from gateway_v2.detect.windowing import max_pattern_length
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from gateway_v2.runtime.holdback_metrics import HoldbackMetrics

    cfg, _ = load_holdback_config({})
    return {
        "scanner": scan, "resolver": MinimalOutputResolver(), "token_index": TokenIndex(),
        "cfg": cfg, "metrics": HoldbackMetrics(), "window": max_pattern_length(),
    }


def _strict(detector: object) -> StrictWithholdPipeline:
    return StrictWithholdPipeline(
        resolver=MinimalOutputResolver(), enforcing_output=True, max_buffer_bytes=1 << 30,
        metrics=StrictMetrics(), clock=time.perf_counter_ns,
        detector=detector,  # type: ignore[arg-type]
    )


def _drive(pipeline: object, chunks: list[UpstreamChunk]) -> _Collector:
    out = _Collector()
    _run(pipeline.run(_source(chunks), out, _never_killed))  # type: ignore[attr-defined]
    return out


def _released(out: _Collector, channel: str) -> str:
    return out.released_text(channel)


def _detector() -> object:
    return _literal_detector(_SECRET, detector_class="aws", category=Category.SECRET)


def test_finish_reason_passes_unscanned_and_unheld_in_the_incremental_pipeline() -> None:
    # a detector that would flag the literal "stop" must never see the meta channel
    detector = _literal_detector("stop", detector_class="aws", category=Category.SECRET)
    pipe = _incremental(detector)
    out = _drive(pipe, [
        UpstreamChunk(text_deltas=(("content", "hello world"),), final=False),
        UpstreamChunk(text_deltas=(("finish_reason", "stop"), ("content", " bye")), final=True),
    ])
    meta = [d for f in out.frames for d in f.text_deltas if d[0] == "finish_reason"]
    assert meta == [("finish_reason", "stop")]
    assert not [f for f in out.frames if f.error_code]
    assert out.released_text("content") == "hello world bye"


def test_finish_reason_is_emitted_last_and_unscanned_in_strict_withhold() -> None:
    detector = _literal_detector("stop", detector_class="aws", category=Category.SECRET)
    out = _drive(_strict(detector), [
        UpstreamChunk(text_deltas=(("finish_reason", "stop"), ("content", "hi")), final=True),
    ])
    [frame] = out.frames
    assert frame.text_deltas == (("content", "hi"), ("finish_reason", "stop"))


def _chunks_for(a_text: str, b_text: str, split: int) -> list[UpstreamChunk]:
    half = len(a_text) // 2 if split else len(a_text)
    return [
        UpstreamChunk(text_deltas=((_ARGS0, a_text[:half]), (_ARGS1, b_text[:3])), final=False),
        UpstreamChunk(text_deltas=((_ARGS0, a_text[half:]), (_ARGS1, b_text[3:])), final=True),
    ]


def test_redacting_call_a_leaves_call_b_byte_identical_in_both_pipelines() -> None:
    # Feature: gw34-sdk-compat, Property 7: per-call isolation in egress
    rng = random.Random(0x6A34_0107)
    for _ in range(120):
        b_text = '{"k":"' + "".join(rng.choice("xyz é") for _ in range(rng.randint(6, 20))) + '"}'
        results: list[tuple[str, str]] = []
        for a_text in ('{"a":"plain value here"}', '{"a":"' + _SECRET + '"}'):
            for make in (_incremental, _strict):
                out = _drive(make(_detector()), _chunks_for(a_text, b_text, rng.randint(0, 1)))
                assert not [f for f in out.frames if f.error_code]
                results.append((make.__name__, _released(out, _ARGS1)))
                if a_text.endswith(_SECRET + '"}'):
                    assert _SECRET not in _released(out, _ARGS0)
        assert {text for _, text in results} == {b_text}


def test_each_call_index_is_a_separate_scan_stream() -> None:
    # a secret split across two DIFFERENT calls is not one match; each stream is scanned alone
    half = len(_SECRET) // 2
    out = _drive(_incremental(_detector()), [
        UpstreamChunk(text_deltas=((_ARGS0, _SECRET[:half]), (_ARGS1, _SECRET[half:])), final=True),
    ])
    assert _released(out, _ARGS0) + _released(out, _ARGS1)
