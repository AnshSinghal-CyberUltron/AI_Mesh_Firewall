"""GW36 S1: ``OpenAIUpstreamProvider`` over ``httpx.MockTransport`` (no socket).

Pins: the rendered body is what is posted (never the internal pack); a non-2xx status, a non-SSE
2xx, an undecodable or truncated stream and a transport failure each end with a typed
``gateway_error``, never content and never an empty success; the SSE ``role`` delta is dropped;
the pooled client is reused within a loop and closed by ``aclose``.
"""

from __future__ import annotations

import asyncio
import json
from types import MappingProxyType
from typing import Any

import httpx
import pytest

from gateway_v2.dispatch.openai_upstream import (
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    CHAT_ENDPOINT,
    OpenAIUpstreamProvider,
)
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain.posture import STREAM_MALFORMED_UPSTREAM
from gateway_v2.edge.wire.sse import SSEDecoder

_BODY = MappingProxyType(
    {
        BODY_DELTAS_KEY: (("message_content", "hi"),),
        BODY_ENDPOINT_KEY: CHAT_ENDPOINT,
        BODY_OPENAI_KEY: MappingProxyType(
            {
                "model": "m",
                "messages": (MappingProxyType({"role": "system", "content": "s"}),),
                "stream": True,
            }
        ),
    }
)


def _sse(*chunks: str, done: bool = True) -> bytes:
    events = [
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": {"role": "assistant", "content": c}}]})
        + "\n\n"
        for c in chunks
    ]
    if done:
        events.append("data: [DONE]\n\n")
    return "".join(events).encode()


def _provider(handler: Any, seen: list[httpx.Request] | None = None) -> OpenAIUpstreamProvider:
    def recording(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        result: httpx.Response = handler(request)
        return result

    return OpenAIUpstreamProvider(
        base_url="http://upstream.test/v1/",
        decoder_factory=SSEDecoder,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording)),
    )


def _events(provider: OpenAIUpstreamProvider, body: Any = _BODY) -> list[UpstreamEvent]:
    async def run() -> list[UpstreamEvent]:
        out = [e async for e in provider.open(UpstreamRequest(destination="amf://x", body=body))]
        await provider.aclose()
        return out

    return asyncio.run(run())


def _ok(*chunks: str, done: bool = True) -> Any:
    return lambda _request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=_sse(*chunks, done=done)
    )


def test_the_rendered_body_is_posted_to_the_endpoint() -> None:
    seen: list[httpx.Request] = []
    events = _events(_provider(_ok("he", "llo"), seen))
    [request] = seen
    assert str(request.url) == "http://upstream.test/v1/chat/completions"
    posted = json.loads(request.content)
    assert posted == {
        "model": "m",
        "messages": [{"role": "system", "content": "s"}],
        "stream": True,
    }
    assert BODY_DELTAS_KEY not in posted
    assert [e.text_deltas for e in events] == [(("content", "he"),), (("content", "llo"),)]
    assert [e.final for e in events] == [False, True]
    assert all(e.gateway_error is None for e in events)


def test_a_body_without_the_rendered_request_posts_nothing() -> None:
    seen: list[httpx.Request] = []
    body = MappingProxyType({BODY_DELTAS_KEY: (("message_content", "raw"),)})
    [event] = _events(_provider(_ok("x"), seen), body)
    assert seen == []
    assert event.gateway_error == "upstream_request_unbuildable"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "upstream_invalid_request"),
        (401, "upstream_auth_error"),
        (403, "upstream_auth_error"),
        (404, "upstream_not_found"),
        (422, "upstream_invalid_request"),
        (429, "upstream_rate_limited"),
        (500, "upstream_error"),
        (503, "upstream_error"),
        (504, "upstream_timeout"),
    ],
)
def test_a_non_2xx_status_is_a_typed_failure(status: int, code: str) -> None:
    handler = lambda _r: httpx.Response(  # noqa: E731
        status, json={"error": {"message": "secret 123-45-6789", "code": "x"}}
    )
    [event] = _events(_provider(handler))
    assert event.gateway_error == code
    assert event.text_deltas == () and event.error_frame is None


def test_a_2xx_that_is_not_an_event_stream_is_malformed() -> None:
    handler = lambda _r: httpx.Response(  # noqa: E731
        200, headers={"content-type": "application/json"}, content=b"{not-json"
    )
    [event] = _events(_provider(handler))
    assert event.gateway_error == STREAM_MALFORMED_UPSTREAM


def test_a_stream_that_ends_without_done_is_malformed_not_a_clean_end() -> None:
    events = _events(_provider(_ok("partial", done=False)))
    assert events[-1].gateway_error == STREAM_MALFORMED_UPSTREAM


def test_an_undecodable_event_is_malformed() -> None:
    handler = lambda _r: httpx.Response(  # noqa: E731
        200, headers={"content-type": "text/event-stream"}, content=b"data: {nope\n\n"
    )
    [event] = _events(_provider(handler))
    assert event.gateway_error == STREAM_MALFORMED_UPSTREAM


def test_a_provider_error_event_keeps_the_scanned_error_frame_path() -> None:
    handler = lambda _r: httpx.Response(  # noqa: E731
        200,
        headers={"content-type": "text/event-stream"},
        content=b'data: {"error": {"code": "server_busy", "message": "m"}}\n\n',
    )
    [event] = _events(_provider(handler))
    assert event.error_frame == "server_busy" and event.gateway_error is None


def test_a_connect_failure_and_a_timeout_are_typed_failures() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    assert _events(_provider(refused))[0].gateway_error == "upstream_unavailable"
    assert _events(_provider(slow))[0].gateway_error == "upstream_timeout"


def test_the_pooled_client_is_reused_within_a_loop_and_closed() -> None:
    made: list[httpx.AsyncClient] = []

    def factory() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(_ok("x")))
        made.append(client)
        return client

    provider = OpenAIUpstreamProvider(
        base_url="http://u/v1", decoder_factory=SSEDecoder, client_factory=factory
    )

    async def two_requests() -> None:
        for _ in range(2):
            async for _event in provider.open(UpstreamRequest(destination="d", body=_BODY)):
                pass
        await provider.aclose()

    asyncio.run(two_requests())
    assert len(made) == 1
    assert made[0].is_closed


def test_closing_a_consumer_mid_read_closes_that_response() -> None:
    closed: list[bool] = []

    class _Stream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def, override]
            yield _sse("one", done=False)
            await asyncio.sleep(3600)

        async def aclose(self) -> None:
            closed.append(True)

    handler = lambda _r: httpx.Response(  # noqa: E731
        200, headers={"content-type": "text/event-stream"}, stream=_Stream()
    )
    provider = _provider(handler)

    async def read_one_then_cancel() -> None:
        iterator = provider.open(UpstreamRequest(destination="d", body=_BODY)).__aiter__()
        task = asyncio.ensure_future(iterator.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await iterator.aclose()  # type: ignore[attr-defined]
        await provider.aclose()

    asyncio.run(read_one_then_cancel())
    assert closed == [True]
