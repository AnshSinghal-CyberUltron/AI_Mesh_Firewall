"""Shared harness for the GW33 offline equivalents (NOT the live gate): real resolver, real holdback
scanner, the real composed output detectors, a stub provider (a list of chunks)."""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.domain import (
    Action,
    Category,
    ExecutionPlan,
    FailurePosture,
    Finding,
    Mode,
    Phase,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.edge.canary_inject import CanaryTicket
from gateway_v2.edge.output_detectors import OutputDetectors, build_output_detectors
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import DownstreamFrame, UpstreamChunk
from gateway_v2.resolve import PlanResolver
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.strict_metrics import StrictMetrics

TOKEN = "cnry_q7w2e3r4t5y6u7i2o3p4a5s6d7"


def contract(p99: float = 20.0) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0, memory_limit=1 << 34, fd_limit=4096, guard_capacity=None,
        target_p99_ms=p99, utilization_cap=0.75, per_worker_rss=400 << 20, worker_override=None,
        cpu_source="t", mem_source="t", fd_source="t",
    )


def builtin(klass: str, **kw: object) -> RuleCondition:
    return RuleCondition(RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", klass, "", **kw)  # type: ignore[arg-type]


def rule(
    rule_id: str,
    category: Category,
    action: Action,
    klass: str | None = None,
    *,
    template: str = "",
    threshold: float | None = None,
    posture: FailurePosture | None = None,
    allow_hosts: tuple[str, ...] = (),
    priority: int = 200,
) -> Rule:
    if posture is None:
        posture = (
            FailurePosture.FAIL_OPEN
            if action in (Action.FLAG, Action.ALLOW)
            else FailurePosture.FAIL_CLOSED
        )
    condition = None if klass is None else builtin(klass, allow_hosts=allow_hosts)
    return Rule(rule_id, category, Mode.ENFORCE, action, threshold, priority, RuleScope.OUTPUT,
                posture, frozenset({Surface.CHAT}), condition, template)


def plan_of(mode: StreamingMode, *rules: Rule) -> ExecutionPlan:
    return ExecutionPlan("org", 1, 1, "abcdef0123456789abcdef", 0.0, tuple(rules), frozenset(),
                         mode, True)


async def _inline(fn: Callable[[], Sequence[Finding]]) -> Sequence[Finding]:
    return fn()


@dataclass
class Result:
    frames: list[DownstreamFrame] = field(default_factory=list)
    stats: object = None

    @property
    def released(self) -> str:
        return "".join(t for f in self.frames if f.error_code is None for _c, t in f.text_deltas)

    def channel(self, name: str) -> str:
        return "".join(
            t for f in self.frames if f.error_code is None for c, t in f.text_deltas if c == name
        )

    @property
    def codes(self) -> list[str]:
        return [f.error_code for f in self.frames if f.error_code is not None]


def chunks_of(
    deltas: Sequence[tuple[str, str]],
) -> list[UpstreamChunk]:
    return [
        UpstreamChunk(text_deltas=(delta,), final=index == len(deltas) - 1)
        for index, delta in enumerate(deltas)
    ]


def split(text: str, rng: random.Random, channel: str = "output_content") -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(text):
        size = rng.randint(1, 9)
        out.append((channel, text[i : i + size]))
        i += size
    return out or [(channel, "")]


def run(
    plan: ExecutionPlan,
    deltas: Sequence[tuple[str, str]],
    *,
    ticket: CanaryTicket | None = None,
    context: tuple[str, ...] = (),
    base: Callable[[str], Sequence[Finding]] | None = None,
    backend: object | None = None,
    killed: Callable[[], bool] = lambda: False,
    composed: OutputDetectors | None = None,
    wrap: Callable[[AsyncIterator[UpstreamChunk]], AsyncIterator[UpstreamChunk]] | None = None,
) -> Result:
    c = contract()
    out = composed or build_output_detectors(
        plan, base=base, ticket=ticket, context_chunks=context,
        backend=backend, contract=c, runner=_inline,  # type: ignore[arg-type]
    )
    cfg, _ = load_holdback_config({})
    pipeline = select_pipeline(
        plan, scanner=holdback_scan, token_index=TokenIndex(), cfg=cfg, metrics=HoldbackMetrics(),
        window=max_pattern_length(), resolver=PlanResolver(plan=plan, phase=Phase.OUTPUT),
        detector=out.detector_for(plan.streaming_mode), max_buffer_bytes=1 << 24,
        strict_metrics=StrictMetrics(), delta_detector=out.canary_delta,
        final_detector=out.final, sync_runner=out.runner,
    )
    result = Result()

    async def send(frame: DownstreamFrame) -> None:
        result.frames.append(frame)

    async def gen():  # type: ignore[no-untyped-def]
        for chunk in chunks_of(deltas):
            yield chunk

    async def drive() -> object:
        source = gen()
        return await pipeline.run(wrap(source) if wrap is not None else source, send, killed)

    result.stats = asyncio.run(drive())
    return result


Runner = Callable[[Sequence[tuple[str, str]]], Awaitable[Result]]
