"""GW33 task 3.6 / D-23, Properties 10 and 16: the stateless per-delta canary overlap scan."""
from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.detect.output.canary import CanaryDetector
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.domain import Action, Category, Finding, Phase, StreamingMode
from gateway_v2.egress.stream import DownstreamFrame, StreamPipeline, UpstreamChunk
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from tests.e2e.gw33_support import TOKEN, contract, plan_of, rule, run, split

_ITERATIONS = 120
_OVERLAP = contract().canary_scan_overlap_chars()


def _plan(action: Action):  # type: ignore[no-untyped-def]
    # The PII redact rule makes the output ENFORCING, so the holdback and the per-delta hook engage
    # even when the canary rule itself is flag-only.
    return plan_of(StreamingMode.INCREMENTAL,
                   rule("out_canary", Category.CANARY, action, "output.canary"),
                   rule("r-pii", Category.PII, Action.REDACT))


def test_a_canary_in_the_middle_of_a_delta_larger_than_the_overlap_is_found() -> None:
    from gateway_v2.edge.canary_inject import CanaryTicket

    big = "x" * (_OVERLAP * 4) + f" {TOKEN} " + "y" * (_OVERLAP * 4)
    result = run(_plan(Action.BLOCK), [("output_content", big)], ticket=CanaryTicket(TOKEN))
    assert result.codes == ["output_blocked"] and TOKEN not in result.released


def test_a_canary_straddling_two_deltas_is_found() -> None:
    from gateway_v2.edge.canary_inject import CanaryTicket

    for cut in range(1, len(TOKEN)):
        deltas = [("output_content", f"ref {TOKEN[:cut]}"), ("output_content", f"{TOKEN[cut:]} end")]
        result = run(_plan(Action.BLOCK), deltas, ticket=CanaryTicket(TOKEN))
        assert result.codes == ["output_blocked"], cut
        assert TOKEN not in result.released


def test_two_channels_never_share_hits() -> None:
    """A hit in `output_reasoning` never appears on `output_content` (offsets are per channel)."""
    # Feature: gw33-output-parity, Property 10: Canary secrecy and detection
    from gateway_v2.edge.canary_inject import CanaryTicket

    def spans(result) -> set[tuple[int, int]]:  # type: ignore[no-untyped-def]
        decision = result.stats.output_decision
        return {
            (f.spans[0].start, f.spans[0].end)
            for f in decision.findings if f.category is Category.CANARY
        }

    reasoning = f"pad {TOKEN} pad"  # canary at offset 4
    content = f"padding padding {TOKEN}"  # canary at offset 16
    only_reasoning = run(_plan(Action.FLAG),
                         [("output_reasoning", reasoning), ("output_content", "pad xxxx pad")],
                         ticket=CanaryTicket(TOKEN))
    assert spans(only_reasoning) == {(4, 4 + len(TOKEN))}  # nothing leaked into the content channel
    assert only_reasoning.channel("output_content") == "pad xxxx pad"
    both = run(_plan(Action.FLAG), [("output_reasoning", reasoning), ("output_content", content)],
               ticket=CanaryTicket(TOKEN))
    assert spans(both) == {(4, 4 + len(TOKEN)), (16, 16 + len(TOKEN))}
    assert both.stats.canary_leaked is True  # type: ignore[attr-defined]


def test_property_16_bounded_per_delta_work_and_no_duplicate_hits() -> None:
    """Feature: gw33-output-parity, Property 16: Bounded per-delta work. Validates 8.3."""
    # Feature: gw33-output-parity, Property 16: Bounded per-delta work and no mid-delta miss
    rng = random.Random(33_016)
    cfg, _ = load_holdback_config({})
    plan = _plan(Action.FLAG)
    for _ in range(_ITERATIONS):
        pad = "".join(rng.choice("abc .\n") for _ in range(rng.randint(0, 900)))
        text = f"{pad} {TOKEN} {pad[:50]}"
        cuts = rng.choice((1, 3, 17, 400, 5000))
        deltas = [("output_content", text[i : i + cuts]) for i in range(0, len(text), cuts)]
        detector = CanaryDetector(TOKEN, rule_id="out_canary")
        scanned = 0

        def delta_detector(whole: str, previous_len: int) -> Sequence[Finding]:
            nonlocal scanned
            start = max(0, previous_len - (_OVERLAP - 1))
            scanned += len(whole) - start
            return detector.detect_from(whole, start)

        pipeline = StreamPipeline.for_plan(
            plan, scanner=holdback_scan, resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
            token_index=TokenIndex(), cfg=cfg, metrics=HoldbackMetrics(),
            window=max_pattern_length(), delta_detector=delta_detector,
        )
        frames: list[DownstreamFrame] = []

        async def send(frame: DownstreamFrame, sink: list[DownstreamFrame] = frames) -> None:
            sink.append(frame)

        async def gen(items: list[tuple[str, str]] = deltas):  # type: ignore[no-untyped-def]
            for index, delta in enumerate(items):
                yield UpstreamChunk(text_deltas=(delta,), final=index == len(items) - 1)

        stats = asyncio.run(pipeline.run(gen(), send, lambda: False))
        assert scanned <= len(text) + len(deltas) * _OVERLAP
        canaries = [f for f in stats.output_decision.findings  # type: ignore[union-attr]
                    if f.category is Category.CANARY]
        keys = [(f.spans[0].start, f.spans[0].end) for f in canaries]
        assert len(canaries) >= 1 and len(keys) == len(set(keys))  # found, never duplicated
        assert keys[0] == (len(pad) + 1, len(pad) + 1 + len(TOKEN))
        assert "".join(t for f in frames for _c, t in f.text_deltas) == text
