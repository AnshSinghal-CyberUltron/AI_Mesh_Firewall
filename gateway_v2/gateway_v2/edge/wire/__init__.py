"""OpenAI request/response types and SSE codec.

The SSE state machine (GW12, task 5.1) lives in :mod:`gateway_v2.edge.wire.sse`:
``SSEEncoder`` / ``SSEDecoder`` plus the SDK-shaped value types ``SSEChunk`` /
``SSEChoice`` / ``SSEErrorFrame``. ``edge/wire`` is the only layer permitted to
render SSE. The OpenAI request/response types land later (GW01/GW15+).
"""

from gateway_v2.edge.wire.sse import (
    SSEChoice,
    SSEChunk,
    SSEDecoder,
    SSEEncoder,
    SSEEncoderTerminated,
    SSEErrorFrame,
)

__all__ = (
    "SSEChoice",
    "SSEChunk",
    "SSEDecoder",
    "SSEEncoder",
    "SSEEncoderTerminated",
    "SSEErrorFrame",
)
