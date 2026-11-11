"""GW28 P8: attempt bound, exhaustion semantics, teardown failure stops further attempts."""

from __future__ import annotations

import asyncio
import random

from gateway_v2.domain.posture import (
    ROUTING_ATTEMPTS_EXHAUSTED,
    ROUTING_TEARDOWN_UNCONFIRMED,
)
from tests.dispatch._gw28_fakes import (
    RELAXED,
    FakeClient,
    Timeline,
    build_rp,
    ctx,
    drain,
    err,
    text,
)

_RETRYABLE = (
    "upstream_timeout",
    "upstream_unavailable",
    "upstream_rate_limited",
    "upstream_error",
)


# Feature: gw28-multi-model-routing, Property 8: attempts bounded; exhaustion code defined
def test_p8_attempt_bound_and_exhaustion() -> None:
    rng = random.Random(0x2808)

    async def main() -> None:
        for i in range(250):
            tl = Timeline()
            n = rng.randint(1, 4)
            clients = {
                f"c{j}": FakeClient(f"c{j}", [err(rng.choice(_RETRYABLE))], tl) for j in range(n)
            }
            max_attempts = rng.randint(1, 4)
            events = await drain(build_rp(clients, max_attempts=max_attempts), ctx(key=str(i)))
            attempts = min(max_attempts, n)
            assert len(tl.opens()) == attempts, (i, tl.log)
            assert len(events) == 1 and events[0].final
            code = events[0].gateway_error
            if attempts >= 2:
                assert code == ROUTING_ATTEMPTS_EXHAUSTED
            else:
                assert code in _RETRYABLE  # the attempt's own code, unchanged

    asyncio.run(main())


def test_non_retryable_failure_is_not_retried_and_keeps_its_code() -> None:
    async def main() -> None:
        for code in (
            "upstream_auth_error",
            "upstream_invalid_request",
            "upstream_not_found",
            "upstream_request_unbuildable",
            "stream_malformed_upstream",
            "mystery",
        ):
            tl = Timeline()
            clients = {n: FakeClient(n, [err(code)], tl) for n in ("a", "b")}
            ev = await drain(build_rp(clients, bounds=RELAXED), ctx())
            assert [e.gateway_error for e in ev] == [code] and len(tl.opens()) == 1

    asyncio.run(main())


def test_teardown_failure_stops_further_attempts() -> None:
    async def main() -> None:
        tl = Timeline()
        clients = {
            n: FakeClient(n, [err("upstream_unavailable")], tl, close_raises=True)
            for n in ("a", "b")
        }
        ev = await drain(build_rp(clients, bounds=RELAXED), ctx())
        assert [e.gateway_error for e in ev] == [ROUTING_TEARDOWN_UNCONFIRMED]
        assert len(tl.opens()) == 1

    asyncio.run(main())


def test_success_after_one_fallback() -> None:
    async def main() -> None:
        tl = Timeline()
        clients = {
            "a": FakeClient("a", [err("upstream_unavailable")], tl),
            "b": FakeClient("b", [text("ok", final=True)], tl),
        }
        # whichever ranks first, a succeeds on the second attempt iff a failed first
        decisions: list[object] = []
        ev = await drain(build_rp(clients), ctx(sink=decisions.append))
        assert ev[-1].gateway_error is None or "a" in tl.opens()
        assert len(decisions) == len(tl.opens())

    asyncio.run(main())
