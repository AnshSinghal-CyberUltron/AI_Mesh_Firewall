"""GW28 P10: injected faults yield exactly one defined terminal and zero further opens."""

from __future__ import annotations

import asyncio
import random

import pytest

import gateway_v2.dispatch.routed_provider as routed_provider
from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import RoutingContext, RoutingDecision, RoutingSnapshot
from gateway_v2.domain.posture import (
    ROUTING_INTERNAL_ERROR,
    ROUTING_SNAPSHOT_UNAVAILABLE,
    ROUTING_TEARDOWN_UNCONFIRMED,
)
from tests.dispatch._gw28_fakes import (
    MAX_LEN,
    RELAXED,
    SMALL,
    FakeClient,
    Timeline,
    build_rp,
    cand,
    cset,
    ctx,
    drain,
    err,
    request,
    role,
    terminals,
    text,
)


def _two(tl: Timeline, script: list = None) -> dict[str, FakeClient]:  # type: ignore[type-arg]
    s = script if script is not None else [err("upstream_unavailable")]
    return {n: FakeClient(n, list(s), tl) for n in ("a", "b")}


class _BoomRouter(ModelRouter):
    def rank(self, *a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("rank boom")


def _rp_with(router: ModelRouter, tl: Timeline) -> RoutedProvider:
    from gateway_v2.dispatch.routing_types import RoutingTable

    return RoutedProvider(
        router=router,
        table=RoutingTable.build({"org": cset(cand("a"), cand("b"))}),
        clients=_two(tl),
        bounds=SMALL,
        max_attempts=2,
        max_ident_len=MAX_LEN,
        snapshot_source=lambda: RoutingSnapshot(),
    )


# Feature: gw28-multi-model-routing, Property 10: injected Exception -> one terminal, no more opens
def test_p10_exceptions_at_each_point() -> None:
    async def main() -> None:
        # rank
        tl = Timeline()
        ev = await drain(_rp_with(_BoomRouter(), tl), ctx())
        assert [e.gateway_error for e in ev] == [ROUTING_INTERNAL_ERROR] and tl.opens() == []
        # snapshot source raising and returning the wrong type
        for source in (lambda: 1 / 0, lambda: "healthy"):
            tl = Timeline()
            rp = build_rp(_two(tl), snapshot=source, bounds=RELAXED)  # type: ignore[arg-type,return-value]
            ev = await drain(rp, ctx())
            assert [e.gateway_error for e in ev] == [ROUTING_SNAPSHOT_UNAVAILABLE]
            assert tl.opens() == []
        # sink raising before content
        tl = Timeline()

        def bad_sink(_d: RoutingDecision) -> None:
            raise RuntimeError("sink boom")

        ev = await drain(build_rp(_two(tl), bounds=RELAXED), ctx(sink=bad_sink))
        assert [e.gateway_error for e in ev] == [ROUTING_INTERNAL_ERROR] and tl.opens() == []
        # mid-iteration exception: live iterator closed, no further open
        tl = Timeline()
        clients = _two(tl, [role(), RuntimeError("mid boom")])
        ev = await drain(build_rp(clients, bounds=RELAXED), ctx())
        assert [e.gateway_error for e in ev] == [ROUTING_INTERNAL_ERROR]
        assert len(tl.opens()) == 1 and tl.live == 0
        # aclose failure
        tl = Timeline()
        clients = {
            n: FakeClient(n, [err("upstream_unavailable")], tl, close_raises=True) for n in "ab"
        }
        ev = await drain(build_rp(clients, bounds=RELAXED), ctx())
        assert [e.gateway_error for e in ev] == [ROUTING_TEARDOWN_UNCONFIRMED]
        assert len(tl.opens()) == 1

    asyncio.run(main())


def test_p10_rebind_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("rebind boom")

    monkeypatch.setattr(routed_provider, "rebind", boom)

    async def main() -> None:
        tl = Timeline()
        ev = await drain(build_rp(_two(tl), bounds=RELAXED), ctx())
        assert [e.gateway_error for e in ev] == [ROUTING_INTERNAL_ERROR] and tl.opens() == []

    asyncio.run(main())


# Feature: gw28-multi-model-routing, Property 10: random mid-stream exceptions: one terminal
def test_p10_random_exceptions_yield_exactly_one_terminal() -> None:
    rng = random.Random(0x2810)

    async def main() -> None:
        for i in range(200):
            tl = Timeline()
            script: list = [  # type: ignore[type-arg]
                *([role()] * rng.randint(0, 2)),
                *([text("x")] * rng.randint(0, 2)),
                RuntimeError("x"),
            ]
            events = await drain(build_rp(_two(tl, script), bounds=RELAXED), ctx(key=str(i)))
            assert (
                len(terminals(events)) == 1 and events[-1].gateway_error == ROUTING_INTERNAL_ERROR
            )
            assert len(tl.opens()) == 1 and tl.live == 0

    asyncio.run(main())


def test_p10_cancellation_propagates_and_closes_live_iterator() -> None:
    async def main() -> None:
        tl = Timeline()
        clients = {"a": FakeClient("a", [], tl, hang_after=True)}
        rp = build_rp(clients, bounds=RELAXED)

        async def consume() -> None:
            async for _ in rp.open_routed(request(), ctx()):
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        assert tl.live == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tl.live == 0 and len(tl.opens()) == 1

    asyncio.run(main())


def test_p10_generator_exit_closes_live_iterator() -> None:
    async def main() -> None:
        tl = Timeline()
        clients = {"a": FakeClient("a", [text("x"), text("y")], tl, hang_after=True)}
        gen = build_rp(clients, bounds=RELAXED).open_routed(request(), ctx())
        first = await gen.__anext__()
        assert first.text_deltas
        await gen.aclose()  # type: ignore[attr-defined]
        assert tl.live == 0

    asyncio.run(main())


def test_post_commit_sink_failure_is_logged_not_raised() -> None:
    rp = build_rp(_two(Timeline()), bounds=RELAXED)

    def bad_sink(_d: RoutingDecision) -> None:
        raise RuntimeError("late sink boom")

    c: RoutingContext = ctx(sink=bad_sink)
    d = RoutingDecision("k", "m", None, None, None, 0, None, "primary", "plan_hrw", 1, None, None)
    rp._emit(c, d, committed=True)  # does not raise
    with pytest.raises(RuntimeError):
        rp._emit(c, d, committed=False)
