# Feature: observability-contract
# Validates: Requirements 1.8, 10.3
"""Wired-endpoint integration (task 12.2) — /metrics typed body + /readyz 503 naming the directory.

Drives the REAL ASGI app assembled by ``build_app`` with the REAL Publisher as its metrics registry
(the task 12.1 wiring), using the shipped in-process ASGI transport idiom from
``test_lgw12_app.py``. Asserts:

* ``/metrics`` returns 200 with the Publisher's typed exposition body computed off the serving loop
  via ``asyncio.to_thread`` (R1.8) — the body carries ``# TYPE`` lines (fully typed) and the
  Publisher's own families.
* ``/readyz`` returns 503 NAMING ``metrics_directory`` when the Publisher's backing directory is
  unavailable — ``build_app`` auto-composes ``make_metrics_readiness`` over the registry's
  ``directory()`` when no explicit readiness probe is passed (R10.3).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient
from gateway_v2.edge.app import build_app
from gateway_v2.edge.readiness import METRICS_DIRECTORY_REASON
from gateway_v2.egress.output_guard import MinimalOutputResolver
from tests.edge._publisher_fixture import build_publisher, small_k_contract

_SEED = 0x0B5E_1002


class _Clock:
    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


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


def _app(publisher: object, *, readiness: object = None) -> object:
    return build_app(
        contract=small_k_contract(),
        provider=StubProviderClient(script=(), rng=random.Random(_SEED)),
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=publisher,  # type: ignore[arg-type]
        clock=_Clock(),
        readiness=readiness,  # type: ignore[arg-type]
    )


def _drive(app: object, method: str, path: str) -> _Transport:
    transport = _Transport()

    async def _run() -> None:
        await app(transport.scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


def test_metrics_returns_the_publisher_typed_body() -> None:
    """``/metrics`` returns the Publisher's fully-typed exposition body (R1.8)."""
    publisher = build_publisher(small_k_contract(), _Clock())
    expected = publisher.exposition()
    app = _app(publisher)

    transport = _drive(app, "GET", "/metrics")
    assert transport.status() == 200, f"/metrics not 200: {transport.status()}"
    body = transport.body().decode("utf-8")
    # The body is typed (carries # TYPE lines) and is the Publisher's own exposition.
    assert "# TYPE " in body, "/metrics body is not typed"
    assert body == expected, "/metrics body is not the Publisher's exposition"


def test_readyz_503_names_metrics_directory_via_autocompose() -> None:
    """With no explicit probe, ``build_app`` composes the directory gate; a down directory 503s.

    The Publisher's backing directory is forced unavailable; ``build_app`` auto-composes
    ``make_metrics_readiness`` over the registry's ``directory()`` (R10.3), so ``/readyz`` 503s and
    the body names ``metrics_directory``.
    """
    publisher = build_publisher(small_k_contract(), _Clock())
    publisher.directory()._available = False  # force the metrics directory unavailable

    app = _app(publisher)  # no explicit readiness => auto-compose over the directory
    transport = _drive(app, "GET", "/readyz")
    assert transport.status() == 503, f"/readyz did not 503 (down directory): {transport.status()}"
    assert METRICS_DIRECTORY_REASON in transport.body().decode("utf-8"), (
        f"503 body does not name the metrics directory: {transport.body()!r}"
    )


def test_readyz_200_when_directory_available_via_autocompose() -> None:
    """With the directory available and state unwired, auto-composed readiness reports 200."""
    publisher = build_publisher(small_k_contract(), _Clock())
    app = _app(publisher)
    transport = _drive(app, "GET", "/readyz")
    assert transport.status() == 200, f"/readyz not 200 with directory up: {transport.status()}"
