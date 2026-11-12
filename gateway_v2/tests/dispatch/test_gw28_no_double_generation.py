"""GW28 P4: no double generation over randomized fault scripts."""

from __future__ import annotations

import asyncio
import random

from gateway_v2.dispatch.provider import UpstreamEvent
from tests.dispatch._gw28_fakes import (
    RELAXED,
    FakeClient,
    Timeline,
    build_rp,
    ctx,
    drain,
    err,
    role,
    text,
)

_ITER = 300
_NAMES = ("a", "b", "c")


def _script(kind: str, who: str) -> tuple[list[UpstreamEvent | float], bool]:
    """(script, hang_after) for a fault kind."""
    return {
        "clean": ([role(), text(f"{who}1"), text(f"{who}2", final=True)], False),
        "fail_before": ([err("upstream_unavailable")], False),
        "rate_limited": ([role(), err("upstream_rate_limited")], False),
        "fail_after": ([text(f"{who}1"), err("upstream_unavailable")], False),
        "stall": ([], True),
        "role_stall": ([role()], True),
        "mid_stall": ([text(f"{who}1")], True),
        "non_retryable": ([err("upstream_auth_error")], False),
    }[kind]


# Feature: gw28-multi-model-routing, Property 4: no double generation
def test_p4_no_double_generation() -> None:
    rng = random.Random(0x2804)
    kinds = (
        "clean",
        "fail_before",
        "rate_limited",
        "fail_after",
        "stall",
        "role_stall",
        "mid_stall",
        "non_retryable",
    )

    async def main() -> None:
        for i in range(_ITER):
            tl = Timeline()
            clients = {}
            for n in _NAMES[: rng.randint(1, 3)]:
                script, hang = _script(rng.choice(kinds), n)
                clients[n] = FakeClient(n, script, tl, hang_after=hang)
            max_attempts = rng.randint(1, 3)
            rp = build_rp(clients, max_attempts=max_attempts)
            events = await drain(rp, ctx(key=f"k{i}"), tl=tl)
            assert tl.max_live <= 1, (i, tl.log)
            assert tl.live == 0, (i, tl.log)
            opens = tl.opens()
            assert len(opens) <= min(max_attempts, len(clients)), (i, tl.log)
            # An open at index>0 is preceded by a close of the previous attempt, and by no
            # content delivered to the consumer.
            seen_open = 0
            closes = 0
            for kind, _who in tl.log:
                if kind == "open":
                    if seen_open > 0:
                        assert closes == seen_open, (i, tl.log)
                    seen_open += 1
                elif kind == "close":
                    closes += 1
                elif kind == "yield" and seen_open >= 1:
                    # after content reached the consumer no further open may follow
                    assert tl.log[-1] is not None
            first_yield = next((j for j, e in enumerate(tl.log) if e[0] == "yield"), None)
            if first_yield is not None:
                assert all(e[0] != "open" for e in tl.log[first_yield:]), (i, tl.log)
            assert len([e for e in events if e.final or e.gateway_error]) <= 1

    asyncio.run(main())


def test_p4_single_open_for_clean_primary() -> None:
    async def main() -> None:
        tl = Timeline()
        clients = {n: FakeClient(n, [text("x", final=True)], tl) for n in ("a", "b")}
        events = await drain(build_rp(clients, bounds=RELAXED), ctx(), tl=tl)
        assert len(tl.opens()) == 1 and events[-1].final

    asyncio.run(main())
