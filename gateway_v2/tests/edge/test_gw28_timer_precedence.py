"""GW28 P11: router timers pre-empt the edge's (streaming + non-streaming), D-13/D-18."""

from __future__ import annotations

import json
import random
import time

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from tests.dispatch._gw28_fakes import FakeClient, Timeline, role, text
from tests.edge import _gw28_app as H

# A 1 ms SLO makes the derived bounds small enough for real-asyncio tests:
# inter_chunk 50 ms, idle 150 ms, router first_event 1.5 s, router gap 40 ms.
_C = H.contract(p99_ms=1.0)

# A 1 ms p99 leaves a 500 us scan budget, and GW34 refuses to start when no scan slot fits it
# (#104). These tests check timers, not calibration, so the scan cost is pinned far below that:
# both in os.environ (`resolve_tool_bounds` reads it) and in every explicit boot env below.
_SCAN_COST_PIN = {"AMF_SCAN_SLOT_COST_US": "40"}


@pytest.fixture(autouse=True)
def _pin_scan_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _SCAN_COST_PIN.items():
        monkeypatch.setenv(name, value)



def _frames(body: str) -> list[str]:
    return [ln[6:] for ln in body.splitlines() if ln.startswith("data: ")]


# Feature: gw28-multi-model-routing, Property 11: router timeout lands first on a stalled stream
def test_p11_never_yielding_stream_ends_with_router_timeout_not_edge_codes() -> None:
    tl = Timeline()
    rp = H.routed({"a": FakeClient("a", [], tl, hang_after=True)}, c=_C)
    t0 = time.perf_counter()
    status, _h, body = H.post(H.app(rp, c=_C), stream=True)
    elapsed = time.perf_counter() - t0
    assert "upstream_timeout" in body, body
    assert "stream_inter_chunk_timeout" not in body and "stream_idle_timeout" not in body
    assert (
        0.9 * _C.upstream_first_event_timeout_s()
        <= elapsed
        < _C.routing_pre_content_budget_s(_C.routing_max_attempts())
    )
    assert tl.live == 0


# Feature: gw28-multi-model-routing, Property 11: fallback after a pre-content stall (streaming)
def test_p11_fallback_after_precontent_stall_is_reachable_on_streaming() -> None:
    tl = Timeline()
    ok = FakeClient("ok", [role(), text("recovered", final=True)], tl)
    stall = FakeClient("stall", [role()], tl, hang_after=True)
    # HRW decides the order; whichever ranks first, the request must end with content.
    rp = H.routed({"stall": stall, "ok": ok}, c=_C)
    status, _h, body = H.post(H.app(rp, c=_C), stream=True)
    assert "recovered" in body and "upstream_timeout" not in body, body
    assert "stream_inter_chunk_timeout" not in body and "stream_idle_timeout" not in body


# Feature: gw28-multi-model-routing, Property 11 (D-18): slow first content is not idle-cut
def test_p11_d18_slow_first_content_is_delivered_without_idle_timeout() -> None:
    delay = 2 * _C.idle_timeout_s()  # 300 ms > idle (150 ms) but << router first_event (1.5 s)
    tl = Timeline()
    rp = H.routed({"a": FakeClient("a", [delay, text("late-content", final=True)], tl)}, c=_C)
    status, _h, body = H.post(H.app(rp, c=_C), stream=True)
    assert "late-content" in body, body
    assert "stream_idle_timeout" not in body and "stream_inter_chunk_timeout" not in body


def test_p11_nonstreaming_never_yielding_upstream_504_within_derived_bound() -> None:
    tl = Timeline()
    rp = H.routed({"a": FakeClient("a", [], tl, hang_after=True)}, c=_C)
    t0 = time.perf_counter()
    status, _h, body = H.post(H.app(rp, c=_C))
    elapsed = time.perf_counter() - t0
    assert status == 504 and json.loads(body)["error"]["code"] == "upstream_timeout"
    assert elapsed < _C.upstream_first_event_timeout_s() + 1.0
    assert tl.live == 0 and len(tl.opens()) == 1  # closed (Req 5.4 local proof)


def test_p11_mid_stream_stall_after_content_is_cut_by_router_gap() -> None:
    tl = Timeline()
    rp = H.routed({"a": FakeClient("a", [text("part")], tl, hang_after=True)}, c=_C)
    t0 = time.perf_counter()
    status, _h, body = H.post(H.app(rp, c=_C), stream=True)
    assert "part" in body and "upstream_timeout" in body
    assert "stream_inter_chunk_timeout" not in body
    assert time.perf_counter() - t0 < 1.0


# --- StreamTimeouts unit regression (routing=None byte-for-byte unchanged) -----------------------
class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _slow(delay: float, value: int = 7) -> int:
    import asyncio

    await asyncio.sleep(delay)
    return value


def test_first_event_s_none_is_unchanged_behaviour() -> None:
    import asyncio

    clock = _Clock()
    st = StreamTimeouts(contract=_C, kill_latch=KillLatch(), clock=clock)
    assert st._first_event_s is None and st._first_pending is False
    # a first await slower than the inter-chunk bound is cut exactly as before
    with pytest.raises(StreamTimeout):
        asyncio.run(st.next_event(_slow(_C.inter_chunk_timeout_s() * 3)))
    # idle clock is NOT re-seeded
    clock.t = 5.0
    st2 = StreamTimeouts(contract=_C, kill_latch=KillLatch(), clock=clock)
    clock.t = 5.0 + 10 * _C.idle_timeout_s()
    assert asyncio.run(st2.next_event(_slow(0.0))) == 7
    assert st2._last_write_s == 5.0


def test_first_event_s_widens_first_await_and_reseeds_idle_once() -> None:
    import asyncio

    clock = _Clock()
    st = StreamTimeouts(contract=_C, kill_latch=KillLatch(), clock=clock, first_event_s=2.0)
    clock.t = 100.0
    assert asyncio.run(st.next_event(_slow(_C.inter_chunk_timeout_s() * 2))) == 7
    assert st._last_write_s == 100.0  # re-seeded after the first await
    clock.t = 200.0
    assert asyncio.run(st.next_event(_slow(0.0))) == 7
    assert st._last_write_s == 100.0  # only once
    with pytest.raises(StreamTimeout):  # later awaits keep the inter-chunk bound
        asyncio.run(st.next_event(_slow(_C.inter_chunk_timeout_s() * 3)))


def test_p11_derivation_over_random_slo_and_attempts() -> None:
    rng = random.Random(0x2811)
    for _ in range(200):
        c = H.contract(p99_ms=rng.uniform(1.0, 100.0))
        for n in range(1, c.routing_max_attempts() + 1):
            assert c.routing_pre_content_budget_s(n) > n * (
                c.upstream_first_event_timeout_s() + c.cancellation_bound_s()
            )
        assert c.upstream_gap_timeout_s() <= c.inter_chunk_timeout_s()


_ = UpstreamEvent
