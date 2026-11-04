"""Response-side length backstop (GW33 D-13, requirement 9.6): built here, not retired.

GW27 clamps the UPSTREAM request (`min(max_tokens, org max_response_tokens, cap)`). A provider that
ignores the clamp could still stream without bound, so `bounded_upstream` counts the characters of
every text delta across channels and, once the sum exceeds `ResourceContract.output_ceiling_chars()`
(`max_output_tokens() x` a deliberately generous characters-per-token bound), raises
`OutputLengthExceeded`. Both pipelines map it to the declared terminal code
`output_length_exceeded`: nothing is silently truncated. Counted in characters because no tokenizer
exists on the stream path; the bound never fires on a compliant provider.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from gateway_v2.egress.output_errors import OutputLengthExceeded
from gateway_v2.egress.stream import UpstreamChunk

__all__ = ("bounded_upstream",)


async def bounded_upstream(
    source: AsyncIterator[UpstreamChunk],
    ceiling_chars: int,
) -> AsyncIterator[UpstreamChunk]:
    """Yield ``source`` unchanged until its cumulative text exceeds ``ceiling_chars``."""
    total = 0
    async for chunk in source:
        total += sum(len(delta) for _channel, delta in chunk.text_deltas)
        if total > ceiling_chars:
            raise OutputLengthExceeded("provider output exceeded the contract ceiling")
        yield chunk
