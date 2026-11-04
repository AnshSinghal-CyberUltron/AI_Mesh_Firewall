"""GW34 Task 10: the disconnect watcher cancels only the inner stream task (D14, D22, R6)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.edge import serve
from tests.edge.asgi_receive import mark_sent, receive_until_sent
from tests.edge.test_gw36_s1_upstream_body import EXAMPLE_PLAN, KEY, _contract

_USER = {"role": "user", "content": "hi"}


class _SlowProvider:
    """Emits one chunk, then blocks until released; records whether its iterator was closed."""

    def __init__(self, *, block_first: bool = False) -> None:
        self.block_first = block_first
        self.opened = asyncio.Event()
        self.closed = False
        self.release = asyncio.Event()

    def open(self, _request: Any) -> Any:
        return self._stream()

    async def _stream(self) -> Any:
        try:
            self.opened.set()
            if self.block_first:
                await self.release.wait()
            yield UpstreamEvent(text_deltas=(("content", "hello "),), final=False)
            await self.release.wait()
            yield UpstreamEvent(text_deltas=(("content", "world"),), final=True)
        finally:
            self.closed = True

    async def abort(self) -> None:
        return None


def _app(provider: Any) -> Any:
    env = {
        "AMF_DEV_PROFILE": "1", "AMF_DEV_API_KEY": KEY, "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1", "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000", "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _run(
    app: Any, *, receive: Any, scope: dict[str, object] | None = None
) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    payload = {"model": "echo-1", "messages": [_USER], "stream": True}
    body = json.dumps(payload).encode()
    scope = scope or {
        "type": "http", "method": "POST", "path": "/v1/chat/completions",
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    scripted = [{"type": "http.request", "body": body, "more_body": False}]
    recv = receive if receive is not None else receive_until_sent(scripted, asyncio.Event())
    await app(scope, recv, send)
    return sent


def test_a_completed_stream_is_unaffected_by_a_disconnect_reported_right_after() -> None:
    # The watcher race: receive_until_sent only reports disconnect once the final frame is sent.
    provider = _SlowProvider()
    provider.release.set()  # no mid-stream pause for this case

    async def go() -> list[dict[str, Any]]:
        done = asyncio.Event()
        scripted = [{"type": "http.request",
                    "body": json.dumps({"model": "echo-1", "messages": [_USER],
                                        "stream": True}).encode(), "more_body": False}]
        recv = receive_until_sent(scripted, done)
        sent: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            mark_sent(done, message)

        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
                 "headers": [(b"authorization", f"Bearer {KEY}".encode()),
                            (b"content-type", b"json")]}
        await _app(provider)(scope, recv, send)
        return sent

    sent = asyncio.run(go())
    bodies = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"[DONE]" in bodies and provider.closed


def test_an_immediate_disconnect_before_any_frame_is_a_real_early_cancel() -> None:
    provider = _SlowProvider(block_first=True)

    delivered = False

    async def immediate_disconnect() -> dict[str, object]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request",
                    "body": json.dumps({"model": "echo-1", "messages": [_USER],
                                        "stream": True}).encode(), "more_body": False}
        return {"type": "http.disconnect"}

    sent = asyncio.run(_run(_app(provider), receive=immediate_disconnect))
    assert provider.closed
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert not starts  # zero bytes sent: cancelled before the first byte


def test_a_disconnect_mid_stream_cancels_the_inner_task_and_releases_resources() -> None:
    provider = _SlowProvider()
    events: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if not events:
            events.append({"type": "http.request",
                           "body": json.dumps({"model": "echo-1", "messages": [_USER],
                                               "stream": True}).encode(), "more_body": False})
            return events[0]
        await provider.opened.wait()  # disconnect only after the first chunk was released
        return {"type": "http.disconnect"}

    sent = asyncio.run(_run(_app(provider), receive=receive))
    assert provider.closed  # the provider iterator was closed, not left running
    bodies = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"[DONE]" not in bodies  # the stream never reached its terminal write


def test_a_second_request_on_the_same_app_succeeds_after_a_cancel() -> None:
    provider = _SlowProvider()
    app = _app(provider)

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    asyncio.run(_run(app, receive=receive))
    second = _SlowProvider()
    second.release.set()
    app2 = _app(second)
    sent = asyncio.run(_run(app2, receive=None))
    bodies = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"[DONE]" in bodies


# ------------------------------------------------- the two cancel branches, unit level


def test_a_watcher_cancel_is_absorbed_and_reports_the_client_left() -> None:
    from gateway_v2.edge.disconnect import run_until_disconnect

    async def go() -> tuple[bool, bool]:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def stream(_done: Any) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def receive() -> dict[str, object]:
            await started.wait()
            return {"type": "http.disconnect"}

        left = await run_until_disconnect(receive, stream)
        return left, cancelled.is_set()

    assert asyncio.run(go()) == (True, True)


def test_a_server_cancel_of_the_handler_cancels_and_awaits_the_inner_task_then_reraises() -> None:
    from gateway_v2.edge.disconnect import run_until_disconnect

    async def go() -> list[asyncio.Task[Any]]:
        started = asyncio.Event()

        async def stream(_done: Any) -> None:
            started.set()
            await asyncio.Event().wait()

        async def receive() -> dict[str, object]:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        handler = asyncio.ensure_future(run_until_disconnect(receive, stream))
        await started.wait()
        handler.cancel()
        try:
            await handler
        except asyncio.CancelledError:
            pass
        else:  # pragma: no cover
            raise AssertionError("a server cancel must be re-raised")
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    assert asyncio.run(go()) == []  # no inner task and no watcher left running


def test_a_disconnect_after_the_stream_finished_is_a_no_op() -> None:
    from gateway_v2.edge.disconnect import run_until_disconnect

    async def go() -> bool:
        async def stream(done: Any) -> None:
            done.set()  # the terminal write begins

        async def receive() -> dict[str, object]:
            return {"type": "http.disconnect"}

        return await run_until_disconnect(receive, stream)

    assert asyncio.run(go()) is False


# ------------------------------------------------- e2e: one release, the bound, withheld calls


class _CountingAdmission:
    """Wraps the real controller: counts ``admit`` and ``complete`` (the request's one release)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.admitted = 0
        self.completed = 0

    async def admit(self, **kwargs: Any) -> Any:
        verdict = await self.inner.admit(**kwargs)
        self.admitted += type(verdict).__name__ == "Admitted"
        return verdict

    def complete(self, request_id: str) -> Any:
        self.completed += 1
        return self.inner.complete(request_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _counted_app(provider: Any) -> tuple[Any, _CountingAdmission]:
    env = {
        "AMF_DEV_PROFILE": "1", "AMF_DEV_API_KEY": KEY, "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1", "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000", "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    counted = _CountingAdmission(deps["admission"])
    deps["admission"] = counted
    return serve._assemble_app(deps), counted


def test_a_completed_stream_and_a_cancelled_stream_each_release_exactly_once() -> None:
    done_provider = _SlowProvider()
    done_provider.release.set()
    app, counted = _counted_app(done_provider)
    sent = asyncio.run(_run(app, receive=None))
    assert b"[DONE]" in b"".join(m.get("body", b"") for m in sent)
    assert (counted.admitted, counted.completed) == (1, 1)

    slow = _SlowProvider()
    app, counted = _counted_app(slow)
    events: list[object] = []

    async def receive() -> dict[str, object]:
        if not events:
            events.append(1)
            return {"type": "http.request", "body": json.dumps(
                {"model": "echo-1", "messages": [_USER], "stream": True}).encode(),
                    "more_body": False}
        await slow.opened.wait()
        return {"type": "http.disconnect"}

    asyncio.run(_run(app, receive=receive))
    assert (counted.admitted, counted.completed) == (1, 1)


def test_the_cancel_lands_within_the_contract_bound() -> None:
    provider = _SlowProvider(block_first=True)
    app, _counted = _counted_app(provider)
    bound = _contract().cancellation_bound_s()
    state: dict[str, float] = {}

    async def go() -> None:
        loop = asyncio.get_running_loop()
        delivered = False

        async def receive() -> dict[str, object]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": json.dumps(
                    {"model": "echo-1", "messages": [_USER], "stream": True}).encode(),
                        "more_body": False}
            await provider.opened.wait()
            state["left"] = loop.time()
            return {"type": "http.disconnect"}

        async def send(_message: dict[str, Any]) -> None:
            return None

        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
                 "headers": [(b"authorization", f"Bearer {KEY}".encode())]}
        await app(scope, receive, send)
        state["returned"] = loop.time()

    asyncio.run(go())
    assert state["returned"] - state["left"] < bound
    assert provider.closed


def test_a_cancel_during_a_withheld_tool_call_releases_nothing() -> None:
    class _ToolThenBlock(_SlowProvider):
        async def _stream(self) -> Any:
            try:
                self.opened.set()
                names = (("output_tool_id#0", "call_1"), ("output_tool_name#0", "get_weather"))
                yield UpstreamEvent(text_deltas=names, final=False)
                yield UpstreamEvent(text_deltas=(("output_tool_arguments#0", '{"a":1}'),),
                                    final=False)
                await self.release.wait()
            finally:
                self.closed = True

    from tests.edge.test_gw34_upstream_body import _pydantic_tool

    provider = _ToolThenBlock()
    app, counted = _counted_app(provider)
    events: list[object] = []

    async def receive() -> dict[str, object]:
        if not events:
            events.append(1)
            return {"type": "http.request", "body": json.dumps(
                {"model": "echo-1", "messages": [_USER], "stream": True,
                 "tools": [_pydantic_tool()]}).encode(), "more_body": False}
        await provider.opened.wait()
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    sent = asyncio.run(_run(app, receive=receive))
    wire = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"call_1" not in wire and b"tool_calls" not in wire and b"[DONE]" not in wire
    assert provider.closed and counted.completed == 1
