"""GW28 edge-test harness: the real ASGI app with passing lifecycle deps and a RoutedProvider."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamRequest
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import RoutingSnapshot, RoutingTable
from gateway_v2.dispatch.stall_guard import StallBounds
from gateway_v2.domain import Finding
from gateway_v2.edge.app import build_app
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from tests.dispatch._gw28_fakes import MAX_LEN, FakeClient, cand, cset
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)

MODEL = "gpt-4o-mini"


def contract(p99_ms: float = 20.0) -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=p99_ms, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


class StubMetrics:
    def exposition(self) -> str:
        return "# gateway_v2 metrics\n"


class RefundBudget(AdmittingBudget):
    """Budget that records refunds (T-edge-3)."""

    def __init__(self) -> None:
        self.refunds: list[str] = []

    def refund(self, cost: int, *, request_id: str, owner: str = "") -> None:
        self.refunds.append(request_id)


class CountingRouted(RoutedProvider):
    """RoutedProvider that counts ``open_routed`` calls and records the request it was handed."""

    __slots__ = ("calls", "requests")

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls = 0
        self.requests: list[UpstreamRequest] = []

    def open_routed(self, request, ctx):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.requests.append(request)
        return super().open_routed(request, ctx)


def routed(
    clients: dict[str, FakeClient],
    *,
    bounds: StallBounds | None = None,
    models: Sequence[str] = (MODEL,),
    max_attempts: int = 2,
    c: ResourceContract | None = None,
    snapshot: Callable[[], RoutingSnapshot] | None = None,
) -> CountingRouted:
    cands = [cand(n, models=models) for n in sorted(clients)]
    table = RoutingTable.build({DEFAULT_ORG_ID: cset(*cands)})
    cc = c or contract()
    return CountingRouted(
        router=ModelRouter(),
        table=table,
        clients=dict(clients),
        bounds=bounds
        or StallBounds(
            first_event_s=cc.upstream_first_event_timeout_s(),
            gap_s=cc.upstream_gap_timeout_s(),
            total_s=cc.upstream_total_timeout_s(),
            teardown_s=cc.cancellation_bound_s(),
        ),
        max_attempts=max_attempts,
        max_ident_len=MAX_LEN,
        snapshot_source=snapshot or (lambda: RoutingSnapshot()),
    )


def no_findings(_t: str) -> Sequence[Finding]:
    return ()


def app(
    rp: RoutedProvider | None,
    *,
    c: ResourceContract | None = None,
    quota: Any = None,
    error_scanner: Callable[[str], Sequence[Finding]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    audit: Callable[[Any], bool] | None = None,
) -> Any:
    import random

    return build_app(
        contract=c or contract(),
        provider=StubProviderClient(script=(), rng=random.Random(0)),
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=StubMetrics(),
        clock=clock,
        error_scanner=error_scanner or no_findings,
        input_detector=no_findings,
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=quota or AdmittingBudget(),
        routing=rp,
        audit=audit,
    )


def post(a: Any, *, model: str = MODEL, stream: bool = False, content: str = "hi there"):  # type: ignore[no-untyped-def]
    """POST one chat completion through the real ASGI app; returns (status, headers, body)."""

    async def run() -> tuple[int, httpx.Headers, str]:
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(transport=transport, base_url="http://t.test") as cl:
            payload: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
            }
            if stream:
                payload["stream"] = True
            r = await cl.post(
                "/v1/chat/completions",
                content=json.dumps(payload),
                headers={"authorization": "Bearer k", "content-type": "application/json"},
            )
            return r.status_code, r.headers, r.text

    return asyncio.run(run())
