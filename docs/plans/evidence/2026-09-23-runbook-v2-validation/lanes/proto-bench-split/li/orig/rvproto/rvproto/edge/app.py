"""Pure ASGI application: lifespan + routing. t0 is taken at ASGI entry, before auth."""

from __future__ import annotations

import asyncio
import contextlib
import time

from rvproto.edge import errors, ops
from rvproto.edge.chat import ChatHandler
from rvproto.edge.http import Receive, Scope, Send, header_map, request_id
from rvproto.edge.state import State, _log, build_state, start_guard


class RvApp:
    def __init__(self) -> None:
        self.st: State | None = None
        self.chat: ChatHandler | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        t0 = time.perf_counter_ns()
        kind = scope["type"]
        if kind == "http":
            st, chat = self.st, self.chat
            assert st is not None and chat is not None
            if scope["path"] == "/v1/chat/completions":
                if scope["method"] == "POST":
                    return await chat.handle(scope, receive, send, t0)
                spec = errors.method_not_allowed(scope["method"])
            elif await ops.handle(st, scope, receive, send):
                return None
            else:
                spec = errors.not_found(scope["path"])
            return await errors.send_error(send, spec, request_id(header_map(scope)))
        if kind == "lifespan":
            return await self._lifespan(receive, send)
        return None

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                try:
                    await self._startup()
                except Exception as exc:
                    _log("startup_failed", error=repr(exc))
                    await send({"type": "lifespan.startup.failed", "message": repr(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await self._shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _startup(self) -> None:
        st = await build_state()
        self.st = st
        self.chat = ChatHandler(st)
        st.tasks = [
            asyncio.create_task(st.ks.run()),
            asyncio.create_task(st.plans.run()),
            asyncio.create_task(st.audit.run()),
            asyncio.create_task(start_guard(st)),
        ]
        st.tasks.append(asyncio.create_task(self._loop_lag(st)))
        if st.s.metrics_dir:
            st.tasks.append(asyncio.create_task(self._dump_metrics(st)))

    async def _loop_lag(self, st: State) -> None:
        """Event-loop lag: how late a short sleep wakes up (GIL holders, sync work, stalls)."""
        period = st.s.ks_refresh_ms / 5000.0
        while True:
            t = time.perf_counter_ns()
            await asyncio.sleep(period)
            st.metrics.observe("loop_lag_ns", max(time.perf_counter_ns() - t - int(period * 1e9), 0))

    async def _dump_metrics(self, st: State) -> None:
        while True:
            await asyncio.sleep(1.0)
            ops._gauges(st)
            st.metrics.dump(st.s.metrics_dir)

    async def _shutdown(self) -> None:
        st = self.st
        if st is None:
            return
        await st.audit.drain(st.s.ks_stale_ms / 1000.0)
        for t in st.tasks:
            t.cancel()
        for t in st.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if st.s.metrics_dir:
            ops._gauges(st)
            st.metrics.dump(st.s.metrics_dir)
        await st.provider.close()
        await st.guard.close()
        await st.redis.aclose()
        await st.redis_bg.aclose()
