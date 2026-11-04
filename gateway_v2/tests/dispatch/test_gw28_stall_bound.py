"""GW28 P7: application-level stall bound (cumulative pre-content, gap, total, teardown)."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from gateway_v2.dispatch.openai_upstream import (
    BODY_ENDPOINT_KEY,
    BODY_OPENAI_KEY,
    UPSTREAM_TIMEOUT,
    OpenAIUpstreamProvider,
)
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing_types import RoutingConfigError
from gateway_v2.dispatch.stall_guard import StallBounds, TeardownUnconfirmed, bounded_events
from gateway_v2.edge.wire.sse import SSEDecoder
from tests.dispatch._gw28_fakes import FakeClient, Timeline, err, request, role, text

# First-event 50 ms, gap 20 ms, total 2 s, teardown 20 ms (design "Timing tolerance").
_B = StallBounds(first_event_s=0.05, gap_s=0.02, total_s=2.0, teardown_s=0.02)
_SLACK = 0.25


async def _collect(
    client: FakeClient, bounds: StallBounds = _B
) -> tuple[list[UpstreamEvent], float]:
    t0 = time.perf_counter()
    out = [e async for e in bounded_events(client.open(request()), bounds)]
    return out, time.perf_counter() - t0


def _within(elapsed: float, bound: float) -> bool:
    return 0.9 * bound <= elapsed <= bound + _SLACK


def _timeout_only(events: list[UpstreamEvent]) -> bool:
    return events[-1].gateway_error == UPSTREAM_TIMEOUT and events[-1].final


def test_bounds_ordering_validated() -> None:
    for bad in ((0.02, 0.02, 2.0), (0.02, 0.05, 2.0), (0.05, 0.02, 0.05), (0.05, 0.0, 2.0)):
        with pytest.raises(RoutingConfigError):
            StallBounds(first_event_s=bad[0], gap_s=bad[1], total_s=bad[2], teardown_s=0.02)
    with pytest.raises(RoutingConfigError):
        StallBounds(first_event_s=0.05, gap_s=0.02, total_s=2.0, teardown_s=0.0)


# Feature: gw28-multi-model-routing, Property 7: a never-yielding upstream is cut at first_event_s
def test_p7_never_yielding_cut_at_first_event() -> None:
    for _ in range(100):
        tl = Timeline()
        events, elapsed = asyncio.run(_collect(FakeClient("a", [], tl, hang_after=True)))
        assert len(events) == 1 and _timeout_only(events)
        assert _within(elapsed, _B.first_event_s)
        assert tl.live == 0 and ("close", "a") in tl.log


# Feature: gw28-multi-model-routing, Property 7: role-only then silence is cut at first_event_s
def test_p7_role_only_then_silence_is_cumulative() -> None:
    for _ in range(100):
        tl = Timeline()
        # role frame at ~0.03 s, then silence: cut at ~0.05 s (cumulative), not 0.03 + gap
        c = FakeClient("a", [0.03, role()], tl, hang_after=True)
        events, elapsed = asyncio.run(_collect(c))
        assert [e.gateway_error for e in events] == [None, UPSTREAM_TIMEOUT]
        assert _within(elapsed, _B.first_event_s)
        assert elapsed < _B.first_event_s + _SLACK


def test_p7_many_role_only_events_do_not_extend_deadline() -> None:
    tl = Timeline()
    c = FakeClient("a", [0.02, role(), 0.02, role(), 0.02, role()], tl, hang_after=True)
    events, elapsed = asyncio.run(_collect(c))
    assert _timeout_only(events) and _within(elapsed, _B.first_event_s)


# Feature: gw28-multi-model-routing, Property 7: mid-stream stall ends at gap_s
def test_p7_mid_stream_stall_ends_at_gap() -> None:
    for _ in range(100):
        tl = Timeline()
        c = FakeClient("a", [text("hi")], tl, hang_after=True)
        events, elapsed = asyncio.run(_collect(c))
        assert events[0].text_deltas and _timeout_only(events)
        assert _within(elapsed, _B.gap_s)
        assert tl.live == 0


# Feature: gw28-multi-model-routing, Property 7: slow trickle ends at total_s
def test_p7_slow_trickle_ends_at_total() -> None:
    b = StallBounds(first_event_s=0.05, gap_s=0.02, total_s=0.3, teardown_s=0.02)
    script = [text("x") if i % 2 == 0 else 0.01 for i in range(2000)]
    events, elapsed = asyncio.run(_collect(FakeClient("a", script, Timeline()), b))
    assert _timeout_only(events) and _within(elapsed, b.total_s)


def test_clean_stream_passes_through_unchanged_and_closes() -> None:
    tl = Timeline()
    sent = [role(), text("a"), text("b", final=True)]
    events, _ = asyncio.run(_collect(FakeClient("a", sent, tl)))
    assert events == sent and tl.live == 0


def test_gateway_error_and_error_frame_are_terminal_and_forwarded() -> None:
    tl = Timeline()
    events, _ = asyncio.run(_collect(FakeClient("a", [err("upstream_unavailable")], tl)))
    assert [e.gateway_error for e in events] == ["upstream_unavailable"]
    frame = UpstreamEvent(text_deltas=(), final=True, error_frame="boom")
    events, _ = asyncio.run(_collect(FakeClient("a", [frame], Timeline())))
    assert events == [frame]


def test_closed_iterator_observation_on_consumer_abandon() -> None:
    async def run() -> Timeline:
        tl = Timeline()
        c = FakeClient("a", [text("a"), text("b"), text("c")], tl, hang_after=True)
        gen = bounded_events(c.open(request()), _B)
        assert (await gen.__anext__()).text_deltas
        await gen.aclose()
        return tl

    tl = asyncio.run(run())
    assert tl.live == 0 and ("close", "a") in tl.log


def test_cancellation_propagates_and_closes() -> None:
    async def run() -> Timeline:
        tl = Timeline()
        c = FakeClient("a", [], tl, hang_after=True)

        async def consume() -> None:
            async for _ in bounded_events(c.open(request()), _B):
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return tl

    assert asyncio.run(run()).live == 0


class _SlowClose:
    """Iterator whose aclose() never completes within teardown_s."""

    def __aiter__(self) -> _SlowClose:
        return self

    async def __anext__(self) -> UpstreamEvent:
        return text("x", final=True)

    async def aclose(self) -> None:
        await asyncio.sleep(3600)


def test_teardown_timeout_raises_unconfirmed_on_normal_exit() -> None:
    async def run() -> None:
        async for _ in bounded_events(_SlowClose(), _B):
            pass

    with pytest.raises(TeardownUnconfirmed):
        asyncio.run(run())


class _HangingBody(httpx.AsyncByteStream):
    """In-process response body that never yields and records whether the connection was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        await asyncio.sleep(3600)
        yield b""

    async def aclose(self) -> None:
        self.closed = True


def test_req_5_4_local_proof_connection_closed_on_timeout() -> None:
    """A stalled real provider stream is cut and its connection closed, not left half-open."""
    body = _HangingBody()

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    prov = OpenAIUpstreamProvider(
        base_url="http://upstream.test/v1",
        decoder_factory=SSEDecoder,
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    req = UpstreamRequest(
        destination="d",
        body={
            BODY_OPENAI_KEY: {"model": "m", "messages": ()},
            BODY_ENDPOINT_KEY: "chat/completions",
        },
    )

    async def run() -> list[UpstreamEvent]:
        out = [e async for e in bounded_events(prov.open(req), _B)]
        await prov.aclose()
        return out

    events = asyncio.run(run())
    assert len(events) == 1 and _timeout_only(events)
    assert body.closed
