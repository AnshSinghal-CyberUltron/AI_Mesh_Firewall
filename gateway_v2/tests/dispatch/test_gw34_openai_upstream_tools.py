"""GW34 Task 6: the OpenAI upstream drain passes tool-call and finish_reason channels through."""
from __future__ import annotations

import asyncio
import json
from functools import partial
from types import MappingProxyType
from typing import Any

import httpx

from gateway_v2.dispatch.openai_upstream import (
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
)
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge.wire.sse import SSEDecoder

_BODY = MappingProxyType({
    BODY_DELTAS_KEY: (("message_content", "hi"),),
    BODY_ENDPOINT_KEY: CHAT_ENDPOINT,
    BODY_OPENAI_KEY: MappingProxyType({"model": "m", "messages": (), "stream": True}),
})


def _chunk(delta: dict[str, Any], finish: str | None = None) -> str:
    choice = {"index": 0, "delta": delta, "finish_reason": finish}
    return "data: " + json.dumps({"choices": [choice]}) + "\n\n"


def _events(sse: str, max_tool_calls: int = 4) -> list[UpstreamEvent]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse.encode())

    provider = OpenAIUpstreamProvider(
        base_url="http://upstream.test/v1/",
        decoder_factory=partial(SSEDecoder, max_tool_calls=max_tool_calls),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def run() -> list[UpstreamEvent]:
        out = [e async for e in provider.open(UpstreamRequest(destination="amf://x", body=_BODY))]
        await provider.aclose()
        return out

    return asyncio.run(run())


def test_role_is_still_dropped_and_tool_and_finish_channels_pass_through() -> None:
    sse = (
        _chunk({"role": "assistant", "content": None,
                "tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "f", "arguments": ""}}]})
        + _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]})
        + _chunk({}, finish="tool_calls")
        + "data: [DONE]\n\n"
    )
    events = _events(sse)
    deltas = [d for e in events for d in e.text_deltas]
    assert ("role", "assistant") not in deltas
    assert ("output_tool_id#0", "c1") in deltas and ("output_tool_name#0", "f") in deltas
    assert ("output_tool_arguments#0", "{}") in deltas
    assert deltas[-1] == ("finish_reason", "tool_calls")
    assert events[-1].final and events[-1].gateway_error is None


def test_a_tools_disabled_decoder_turns_tool_calls_into_a_typed_failure() -> None:
    sse = _chunk({"tool_calls": [{"index": 0, "id": "c1"}]}) + "data: [DONE]\n\n"
    [event] = _events(sse, max_tool_calls=0)
    assert event.gateway_error == "stream_malformed_upstream" and not event.text_deltas
