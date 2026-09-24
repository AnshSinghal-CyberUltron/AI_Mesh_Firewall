"""SSE state machine — rb.md §10.5.5 (L2236); mode from plan L2196/L2835."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.dispatch.provider import ProviderStream
    from gateway_v2.plan.model import ExecutionPlan, StreamingMode

# L2236 'async def run(self, upstream: ProviderStream, decision_ctx: OutputContext)'

# L2196 ExecutionPlan.streaming_mode: StreamingMode  # INCREMENTAL | STRICT_WITHHOLD


@dataclass(frozen=True, slots=True)
class OutputContext:
    plan: ExecutionPlan


class StreamPipeline:
    async def run(
        self, upstream: ProviderStream, decision_ctx: OutputContext
    ) -> AsyncIterator[bytes]:
        strict = decision_ctx.plan.streaming_mode is StreamingMode.STRICT_WITHHOLD
        held: list[bytes] = []
        async for chunk in upstream:
            if strict:
                held.append(chunk)
            else:
                yield chunk
        for chunk in held:
            yield chunk
