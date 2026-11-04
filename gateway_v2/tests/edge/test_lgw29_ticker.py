"""GW29 IncidentTicker: sweeps on an injected sleep, survives a failed sweep, stops cleanly."""
from __future__ import annotations

import asyncio

from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.edge.notifier_pump import IncidentTicker


class Sweeper:
    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.calls = 0
        self.fail_on = fail_on or set()

    def sweep(self) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("sweep bug")


def test_tick_counts_a_failure_and_the_next_tick_still_runs() -> None:
    sweeper, metrics = Sweeper({1}), IncidentMetrics()
    ticker = IncidentTicker(sweeper=sweeper, period_s=1.0, sleep=_never, metrics=metrics)
    ticker.tick()
    ticker.tick()
    assert sweeper.calls == 2
    assert metrics.series()["amf_incident_ticker_failures_total"] == 1.0


async def _never(delay: float) -> None:  # pragma: no cover - never awaited in tick tests
    raise AssertionError("tick must not sleep")


def test_run_sleeps_the_period_between_sweeps_and_stops_on_cancel() -> None:
    async def scenario() -> tuple[list[float], int]:
        slept: list[float] = []
        sweeper = Sweeper({2})
        gate = asyncio.Event()

        async def sleep(delay: float) -> None:
            slept.append(delay)
            if len(slept) >= 4:
                gate.set()
                await asyncio.sleep(3600)
            await asyncio.sleep(0)

        ticker = IncidentTicker(
            sweeper=sweeper, period_s=5.0, sleep=sleep, metrics=IncidentMetrics(),
        )
        ticker.start()
        await asyncio.wait_for(gate.wait(), timeout=5)
        await ticker.stop()
        return slept, sweeper.calls

    slept, calls = asyncio.run(scenario())
    assert slept[:3] == [5.0, 5.0, 5.0] and calls == 3, "a failed 2nd sweep did not end the loop"


def test_the_period_comes_from_the_contract_bucket_width() -> None:
    from tests.admit._lgw29_support import contract
    c = contract()
    assert c.risk_bucket_s() * c.risk_buckets() == c.risk_window_s()
