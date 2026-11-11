# Feature: observability-contract
# Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5
"""Readiness 2x2 matrix + 503 body naming the metrics directory (task 9.3).

Example/unit tests complementing the Property 11 loop: the explicit 2x2 (state ready/not-ready x
directory available/unavailable) and the ``/readyz`` 503 body naming ``metrics_directory`` when the
directory is the unavailable dependency, driven through the real ASGI app.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient
from gateway_v2.edge.app import build_app
from gateway_v2.edge.readiness import METRICS_DIRECTORY_REASON, make_metrics_readiness
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.state_stamp import StampView
from tests.edge._publisher_fixture import build_publisher, small_k_contract

_SEED = 0x0B5E_1003


class _StubDirectory:
    __slots__ = ("_available",)

    def __init__(self, available: bool) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available


def _ready_view() -> None:
    return None


def _unready_view() -> StampView:
    return StampView(secret=b"test-secret", clock=lambda: 1000.0)


def test_readiness_2x2_matrix() -> None:
    """All four combinations of (state ready) x (directory available) (R10.2/R10.4/R10.5)."""
    # state ready + directory up => ready.
    probe = make_metrics_readiness(_ready_view(), _StubDirectory(True))
    assert probe() == (True, None)

    # state ready + directory down => not ready, naming the metrics directory (R10.1).
    probe = make_metrics_readiness(_ready_view(), _StubDirectory(False))
    assert probe() == (False, METRICS_DIRECTORY_REASON)

    # state not ready + directory up => not ready, STATE reason (state gated first, R10.4).
    probe = make_metrics_readiness(_unready_view(), _StubDirectory(True))
    ready, reason = probe()
    assert ready is False
    assert reason is not None and reason != METRICS_DIRECTORY_REASON

    # state not ready + directory down => not ready, STATE reason (state wins, R10.4).
    probe = make_metrics_readiness(_unready_view(), _StubDirectory(False))
    ready, reason = probe()
    assert ready is False
    assert reason is not None and reason != METRICS_DIRECTORY_REASON


# --------------------------------------------------------------------------- #
# /readyz 503 body naming the metrics directory, through the real ASGI app.
# --------------------------------------------------------------------------- #


class _Transport:
    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    @staticmethod
    def scope(method: str, path: str) -> dict[str, object]:
        return {"type": "http", "method": method, "path": path, "headers": []}

    async def receive(self) -> Mapping[str, object]:
        return {"type": "http.disconnect"}

    async def send(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))

    def status(self) -> int:
        start = next(e for e in self.events if e.get("type") == "http.response.start")
        status = start.get("status")
        assert isinstance(status, int)
        return status

    def body(self) -> bytes:
        out = bytearray()
        for e in self.events:
            if e.get("type") == "http.response.body":
                b = e.get("body")
                if isinstance(b, (bytes, bytearray)):
                    out.extend(b)
        return bytes(out)


class _Clock:
    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _drive(app: object, method: str, path: str) -> _Transport:
    transport = _Transport()

    async def _run() -> None:
        await app(transport.scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


def test_readyz_503_names_metrics_directory_when_unavailable() -> None:
    """``/readyz`` 503s and the body names ``metrics_directory`` when the directory is down (R10.3).

    The real ASGI app is built with the real Publisher as its metrics registry and a readiness
    probe composed over a down directory; the shipped ``_readyz`` renders the (False, reason) pair
    as a 503 whose body carries the reason verbatim.
    """
    contract = small_k_contract()
    publisher = build_publisher(contract, _Clock())
    probe = make_metrics_readiness(_ready_view(), _StubDirectory(False))

    app = build_app(
        contract=contract,
        provider=StubProviderClient(script=(), rng=random.Random(_SEED)),
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=publisher,
        clock=_Clock(),
        readiness=probe,
    )
    transport = _drive(app, "GET", "/readyz")
    assert transport.status() == 503, f"/readyz did not 503: {transport.status()}"
    assert METRICS_DIRECTORY_REASON in transport.body().decode("utf-8"), (
        f"503 body does not name the metrics directory: {transport.body()!r}"
    )


def test_readyz_200_when_both_gates_ready() -> None:
    """``/readyz`` returns 200 when state is ready and the directory is available (R10.2)."""
    contract = small_k_contract()
    publisher = build_publisher(contract, _Clock())
    probe = make_metrics_readiness(_ready_view(), _StubDirectory(True))
    app = build_app(
        contract=contract,
        provider=StubProviderClient(script=(), rng=random.Random(_SEED)),
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=publisher,
        clock=_Clock(),
        readiness=probe,
    )
    transport = _drive(app, "GET", "/readyz")
    assert transport.status() == 200, f"/readyz not 200 when ready: {transport.status()}"
