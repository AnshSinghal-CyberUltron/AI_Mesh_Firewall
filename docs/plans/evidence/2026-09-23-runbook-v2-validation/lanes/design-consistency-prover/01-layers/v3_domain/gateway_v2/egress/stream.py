"""SSE pipeline; mode from the plan; output guard injected."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.ports import ProviderStream
from gateway_v2.domain.taxonomy import Disposition, StreamingMode
from gateway_v2.egress.output_guard import OutputGuard


@dataclass(frozen=True, slots=True)
class OutputContext:
    plan: ExecutionPlan
    guard: OutputGuard


class StreamPipeline:
    async def run(self, upstream: ProviderStream, ctx: OutputContext) -> AsyncIterator[bytes]:
        strict = ctx.plan.streaming_mode is StreamingMode.STRICT_WITHHOLD
        held = b""
        async for chunk in upstream:
            if not strict:
                yield chunk
            held += chunk
        verdict = ctx.guard.check(held.decode("utf-8", "replace"), ctx.plan)
        if strict and verdict.disposition is not Disposition.BLOCK:
            yield held
