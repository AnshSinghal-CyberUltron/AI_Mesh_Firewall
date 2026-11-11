"""GW28 P5: stream integrity of the routed event stream."""

from __future__ import annotations

import asyncio
import random

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.edge.wire.sse import SSEDecoder, SSEEncoder
from gateway_v2.egress.stream import DownstreamFrame
from tests.dispatch._gw28_fakes import (
    RELAXED,
    FakeClient,
    Timeline,
    build_rp,
    ctx,
    drain,
    err,
    role,
    terminals,
    text,
)

_SENTINEL = "ABANDONED-ATTEMPT-SENTINEL"


def _client(kind: str, who: str, tl: Timeline) -> tuple[FakeClient, list[str]]:
    """A client of a fault kind plus the content texts it can legitimately deliver."""
    if kind == "clean":
        return FakeClient(who, [role(), text(f"{who}1"), text(f"{who}2", final=True)], tl), [
            f"{who}1",
            f"{who}2",
        ]
    if kind == "fail_after":
        return FakeClient(who, [text(f"{who}1"), err("upstream_unavailable")], tl), [f"{who}1"]
    if kind == "fail_before":
        return FakeClient(who, [role(), err("upstream_unavailable")], tl), []
    if kind == "late_stall":  # would send the sentinel, but is cut before it arrives
        return FakeClient(who, [0.2, text(_SENTINEL)], tl, hang_after=True), []
    return FakeClient(who, [role()], tl, hang_after=True), []  # role_stall


# Feature: gw28-multi-model-routing, Property 5: stream integrity
def test_p5_stream_integrity() -> None:
    rng = random.Random(0x2805)
    kinds = ("clean", "fail_after", "fail_before", "late_stall", "role_stall")

    async def main() -> None:
        for i in range(250):
            tl = Timeline()
            clients, texts = {}, {}
            for n in ("a", "b", "c")[: rng.randint(1, 3)]:
                clients[n], texts[n] = _client(rng.choice(kinds), n, tl)
            events = await drain(build_rp(clients, max_attempts=rng.randint(1, 3)), ctx(key=str(i)))
            term = terminals(events)
            assert len(term) <= 1, (i, events)
            if term:
                assert events[-1] is term[0], (i, events)  # nothing after the terminal
            delivered = [d for e in events for _c, d in e.text_deltas]
            assert _SENTINEL not in "".join(delivered)
            if delivered:
                # content belongs to exactly one committing attempt, in order
                assert any(delivered == texts[n][: len(delivered)] for n in texts), (i, delivered)
                # and no error event precedes content
                first_err = next((j for j, e in enumerate(events) if e.gateway_error), None)
                last_txt = max(j for j, e in enumerate(events) if e.text_deltas)
                assert first_err is None or first_err > last_txt
            else:
                assert term and term[0].gateway_error is not None, (i, events)

    asyncio.run(main())


def test_p5_round_trips_through_sse_codec() -> None:
    async def main() -> None:
        tl = Timeline()
        c = FakeClient("a", [role(), text("he"), text("llo", final=True)], tl)
        events = await drain(build_rp({"a": c}, bounds=RELAXED), ctx())
        enc = SSEEncoder()
        wire = b"".join(
            enc.content(DownstreamFrame(text_deltas=e.text_deltas)) for e in events if e.text_deltas
        )
        wire += enc.done()
        dec = SSEDecoder()
        frames = dec.feed(wire)
        got = "".join(d for f in frames for ch, d in f.text_deltas if ch != "role")
        assert got == "hello" and dec.finished()

    asyncio.run(main())


def test_empty_iterator_and_no_final_are_malformed() -> None:
    async def main() -> None:
        ev = await drain(build_rp({"a": FakeClient("a", [], Timeline())}, bounds=RELAXED), ctx())
        assert [e.gateway_error for e in ev] == ["stream_malformed_upstream"]
        ev = await drain(
            build_rp({"a": FakeClient("a", [text("x")], Timeline())}, bounds=RELAXED), ctx()
        )
        assert ev[0].text_deltas and ev[-1].gateway_error == "stream_malformed_upstream"
        assert len(terminals(ev)) == 1

    asyncio.run(main())


def test_provider_error_frame_is_forwarded_not_retried() -> None:
    async def main() -> None:
        tl = Timeline()
        frame = UpstreamEvent(text_deltas=(), final=True, error_frame="provider-said-no")
        clients = {"a": FakeClient("a", [frame], tl), "b": FakeClient("b", [frame], tl)}
        ev = await drain(build_rp(clients, bounds=RELAXED), ctx())
        assert ev == [frame] and len(tl.opens()) == 1

    asyncio.run(main())
