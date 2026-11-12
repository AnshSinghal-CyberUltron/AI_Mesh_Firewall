"""The state plane's catch-all handlers log the exception TYPE, never its text.

The text of an exception raised while reading tenant records is not ours to vouch for: a decoder
quotes the bytes it choked on, a `CompileError` quotes rule ids, a driver error can quote a DSN.
Before fix/state-applier-isolation these handlers wrote `%s: %s` with the exception itself:

* `edge/state_runtime.py` -- the state loop (`state cycle failed`) and lifespan start-up;
* `state_control/projector_service.py` -- `projection round failed`;
* `state_control/rehydrate.py` -- `rehydrate round failed for kind=...` (and its summary).

Each test raises an exception whose message is a canary and asserts the canary is nowhere in
what was logged or reported, while the type still is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import fakeredis
import pytest

from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import StateKnobs
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig, StateServedApp
from state_control import projector_service
from state_control.db import MemoryControlDB
from state_control.projector import ProjectionSummary
from state_control.publisher import MemoryStore
from state_control.rehydrate import Rehydrator
from state_control.writer import StateWriter
from tests.state_control.test_gw36_s2_service import BASE

CANARY = "CANARY tenant payload 7f3a"
SECRET = b"type-only-logging"


class _Canary(Exception):
    pass


def _messages(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)


def test_the_state_loop_logs_a_failed_cycle_by_type(caplog: pytest.LogCaptureFixture) -> None:
    async def go() -> None:
        runtime = StateRuntime(
            StateRuntimeConfig(
                "redis://unused", SECRET, "{rv2}", StateKnobs(refresh_period_ms=5),
            ),
            client=fakeredis.FakeAsyncRedis(),
        )

        async def cycle() -> tuple[()]:
            raise _Canary(CANARY)

        runtime.cycle = cycle  # type: ignore[method-assign]
        task = asyncio.create_task(runtime._run())  # noqa: SLF001 - the loop under test
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert runtime.cycle_failures >= 1

    with caplog.at_level(logging.DEBUG, logger="amf"):
        asyncio.run(go())
    text = _messages(caplog)
    assert "state cycle failed: _Canary" in text
    assert CANARY not in text


def test_a_runtime_that_cannot_start_logs_by_type(caplog: pytest.LogCaptureFixture) -> None:
    class _Runtime:
        async def startup(self) -> None:
            raise _Canary(CANARY)

        async def shutdown(self) -> None:
            return None

    async def app(scope: object, receive: object, send: object) -> None:
        await receive()  # type: ignore[operator]

    async def go() -> None:
        served = StateServedApp(app, _Runtime())  # type: ignore[arg-type]

        async def receive() -> dict[str, object]:
            return {"type": "lifespan.startup"}

        async def send(message: object) -> None:
            del message

        await served({"type": "lifespan"}, receive, send)

    with caplog.at_level(logging.DEBUG, logger="amf"):
        asyncio.run(go())
    text = _messages(caplog)
    assert "state runtime did not start: _Canary" in text
    assert CANARY not in text


def test_a_failed_projection_round_is_logged_by_type(caplog: pytest.LogCaptureFixture) -> None:
    class _Raising:
        def round_once(self) -> ProjectionSummary:
            raise _Canary(CANARY)

    config = projector_service.from_env(BASE)
    service = projector_service.ProjectorService(config, _Raising())  # type: ignore[arg-type]

    with caplog.at_level(logging.DEBUG, logger="amf"):
        assert service.round_once() is None

    text = _messages(caplog)
    assert "projection round failed: _Canary" in text
    assert CANARY not in text


def test_a_failed_rehydrate_kind_is_logged_and_reported_by_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Broken(MemoryStore):
        def stored_head(self, kind: StateKind) -> object:  # type: ignore[override]
            raise _Canary(CANARY)

    db = MemoryControlDB()
    store = _Broken()
    rehydrator = Rehydrator(
        db, store, StateWriter(db, store, SECRET), SECRET, stale_grace_s=0.0,
        kinds=(StateKind.PLAN,),
    )

    with caplog.at_level(logging.DEBUG, logger="amf"):
        summary = rehydrator.round_once()

    assert summary.errors == ((StateKind.PLAN, "_Canary"),)
    text = _messages(caplog)
    assert "rehydrate round failed for kind=plan: _Canary" in text
    assert CANARY not in text
