"""GW32 WebSocket transport: same pipeline as HTTP, close codes, bounds, revocation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.admit.mcp_cap import CallCapTable  # noqa: E402
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics  # noqa: E402
from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import IdentityOutcome, TenantIdentity  # noqa: E402
from gateway_v2.edge.mcp_wiring import route_of, wrap_mcp_app  # noqa: E402
from gateway_v2.edge.mcp_ws import WsLimiter  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.resources_mcp import derive_cap_bounds, derive_transport_bounds  # noqa: E402
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402


class Identity:
    def __init__(self, principal: Principal | None = None) -> None:
        self.revoked = False
        self.principal = principal or Principal("kid", ORG, 0.0, 0.0, 1)

    async def resolve(self, scope: object) -> TenantIdentity | None:
        return (await self.resolve_outcome(scope)).identity

    async def resolve_outcome(self, scope: object) -> IdentityOutcome:
        headers = dict(scope["headers"])  # type: ignore[index]
        if self.revoked or b"authorization" not in headers:
            return IdentityOutcome.invalid()
        return IdentityOutcome.resolved(TenantIdentity(ORG, self.principal))


def _build(identity: Identity, *, tb_over: dict | None = None, cap: bool = False):  # type: ignore[no-untyped-def]
    import fakeredis

    broker = Broker()
    bounds = replace(derive_transport_bounds(_contract()), **(tb_over or {}))
    table = None
    if cap:
        table = CallCapTable(
            client=fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()),
            keys=McpStoreKeys(),
            bounds=derive_cap_bounds(_contract()),
            clock=lambda: 0.0,
            rng=__import__("random").Random(1),
            metrics=McpPolicyMetrics(),
            spawn=lambda coro: coro.close(),
        )
    deps = replace(
        mcp_deps(broker, Control([server_row("s")])), transport_bounds=bounds, cap_table=table
    )
    app = build_app(
        contract=_contract(),
        provider=None,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=_none,
        error_scanner=_none,
        identity=identity,  # type: ignore[arg-type]
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        mcp=deps,
    )
    return app, broker, bounds


def drive(app, frames, *, path="/v1/mcp/s", headers=None, between=None):  # type: ignore[no-untyped-def]
    """Connect, send each frame, collect everything the server sent. `between(i)` runs before
    frame i is sent (to mutate state). Ends with a disconnect."""
    sent: list[dict[str, object]] = []
    hdrs = {"authorization": "Bearer test", "host": "gw.test", **(headers or {})}

    async def go() -> None:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        await queue.put({"type": "websocket.connect"})

        async def receive() -> dict[str, object]:
            return await queue.get()

        async def send(event: dict[str, object]) -> None:
            sent.append(event)

        scope = {
            "type": "websocket",
            "path": path,
            "raw_path": path.encode(),
            "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
            "client": ("1.2.3.4", 1),
        }
        task = asyncio.ensure_future(app(scope, receive, send))
        for i, frame in enumerate(frames):
            if between:
                between(i)
            await queue.put(frame)
            await asyncio.sleep(0.02)
        await queue.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 5)

    asyncio.run(go())
    return sent


def text(obj: object) -> dict[str, object]:
    return {"type": "websocket.receive", "text": json.dumps(obj)}


def closes(sent) -> list[int]:  # type: ignore[no-untyped-def]
    return [e["code"] for e in sent if e["type"] == "websocket.close"]  # type: ignore[misc]


def texts(sent) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(e["text"]) for e in sent if e["type"] == "websocket.send"]


def test_messages_run_the_http_pipeline_and_reply_as_text_frames() -> None:
    app, broker, bounds = _build(Identity())
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))
    sent = drive(wrapped, [text(call("ping", msg_id=1)), text(call("tools/list", msg_id=2))])
    assert sent[0]["type"] == "websocket.accept"
    replies = texts(sent)
    assert [r["id"] for r in replies] == [1, 2]
    assert len(broker.requests) == 1  # tools/list reached the broker through the same pipeline
    assert closes(sent) == []


def test_gateway_path_and_org_mismatch() -> None:
    app, _, bounds = _build(Identity())
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))
    ok = drive(wrapped, [text(call("ping"))], path="/gateway/acme/mcp/s")
    assert ok[0]["type"] == "websocket.accept"
    bad = drive(wrapped, [text(call("ping"))], path="/gateway/wrong/mcp/s")
    assert closes(bad) == [1008] and all(e["type"] != "websocket.accept" for e in bad)


def test_revoked_or_unauthenticated_upgrade_is_closed_without_accept() -> None:
    revoked = Identity()
    revoked.revoked = True
    app, broker, bounds = _build(revoked)
    out = drive(wrap_mcp_app(app, WsLimiter(bounds)), [text(call("tools/list"))])
    assert closes(out) == [1008] and broker.requests == []
    assert all(e["type"] != "websocket.accept" for e in out)


def test_origin_is_refused() -> None:
    app, broker, bounds = _build(Identity())
    out = drive(
        wrap_mcp_app(app, WsLimiter(bounds)), [], headers={"origin": "https://evil.example"}
    )
    assert closes(out) == [1008] and broker.requests == []


def test_frame_rules_binary_oversize_and_batch() -> None:
    app, _, bounds = _build(Identity())
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))
    binary = drive(wrapped, [{"type": "websocket.receive", "bytes": b"x"}])
    assert closes(binary) == [1003]
    big = {"type": "websocket.receive", "text": "x" * (bounds.ws_frame_bytes + 1)}
    assert closes(drive(wrapped, [big])) == [1009]
    batch = drive(
        wrapped, [{"type": "websocket.receive", "text": "[" + json.dumps(call("ping")) + "]"}]
    )
    (reply,) = texts(batch)
    assert reply["error"]["data"]["code"] == "mcp_invalid_request"


def test_connection_bounds_close_1013() -> None:
    app, _, bounds = _build(Identity())
    assert closes(drive(wrap_mcp_app(app, None), [])) == [1013]  # no limiter wired: fail closed
    limiter = WsLimiter(replace(bounds, ws_per_key=1))
    assert limiter.acquire("kid", ORG)
    assert closes(drive(wrap_mcp_app(app, limiter), [])) == [1013]
    limiter.release("kid", ORG)
    assert limiter.open_count() == 0
    drive(wrap_mcp_app(app, limiter), [text(call("ping"))])
    assert limiter.open_count() == 0  # released when the socket ends


def test_revocation_between_frames_closes_the_socket() -> None:
    identity = Identity()
    app, broker, bounds = _build(identity)
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))

    def revoke(i: int) -> None:
        identity.revoked = i == 1

    sent = drive(wrapped, [text(call("tools/list")), text(call("tools/list"))], between=revoke)
    assert len(broker.requests) == 1 and closes(sent) == [1008]


def test_allow_list_and_cap_bind_over_websocket() -> None:
    principal = Principal("kid", ORG, 0.0, 0.0, 1, frozenset({"a"}), 1)
    app, broker, bounds = _build(Identity(principal), cap=True)
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))
    denied = text(call("tools/call", {"name": "b", "arguments": {}}))
    allowed = text(call("tools/call", {"name": "a", "arguments": {}}))
    sent = drive(wrapped, [denied, allowed, allowed])
    codes = [r.get("error", {}).get("data", {}).get("code") for r in texts(sent)]
    assert codes[0] == "mcp_tool_not_permitted"
    assert codes[1] is None  # bootstrap allowance admits one cold call
    assert codes[2] == "mcp_tool_call_cap_exceeded"
    assert len(broker.requests) == 1


def test_other_websocket_paths_are_delegated_to_the_app() -> None:
    app, _, bounds = _build(Identity())
    sent = drive(wrap_mcp_app(app, WsLimiter(bounds)), [], path="/v1/not-mcp")
    assert all(e["type"] != "websocket.accept" for e in sent)
    assert route_of(app) is not None and route_of(object()) is None


def test_accept_event_stream_on_the_upgrade_still_gets_json_frames() -> None:
    app, broker, bounds = _build(Identity())
    wrapped = wrap_mcp_app(app, WsLimiter(bounds))
    sent = drive(
        wrapped, [text(call("tools/list", msg_id=7))],
        headers={"accept": "application/json, text/event-stream"},
    )
    (reply,) = texts(sent)  # parsed as one JSON document, not an SSE-framed body
    assert reply["id"] == 7 and closes(sent) == []
    assert len(broker.requests) == 1


def test_revocation_while_a_message_is_in_flight_cancels_it_and_closes_1008() -> None:
    identity = Identity()
    app, broker, bounds = _build(identity, tb_over={"stream_recheck_s": 0.01})
    started = asyncio.Event()
    finished: list[bool] = []
    real = broker.__call__

    async def slow(*args: object) -> tuple[int, bytes]:
        started.set()
        await asyncio.sleep(2)  # held until the watchdog cancels it
        finished.append(True)
        return await real(*args)  # type: ignore[arg-type]

    route = route_of(app)
    assert route is not None
    route.broker._transport = slow  # type: ignore[assignment]  # noqa: SLF001
    sent: list[dict[str, object]] = []

    async def go() -> None:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        await queue.put({"type": "websocket.connect"})
        scope = {
            "type": "websocket", "path": "/v1/mcp/s", "raw_path": b"/v1/mcp/s",
            "headers": [(b"authorization", b"Bearer test"), (b"host", b"gw.test")],
            "client": ("1.2.3.4", 1),
        }

        async def send(event: dict[str, object]) -> None:
            sent.append(event)

        task = asyncio.ensure_future(
            wrap_mcp_app(app, WsLimiter(bounds))(scope, queue.get, send)
        )
        await queue.put(text(call("tools/list")))
        await asyncio.wait_for(started.wait(), 2)
        identity.revoked = True
        await asyncio.wait_for(task, 2)

    asyncio.run(go())
    assert closes(sent) == [1008]
    assert finished == []  # the in-flight call was cancelled, never completed
    assert texts(sent) == []


def test_idle_weight_bounds_open_sockets_against_the_slot_budget() -> None:
    _, _, bounds = _build(Identity())
    weighted = replace(bounds, ws_idle_weight=0.5, ws_global=100, ws_per_key=100, ws_per_org=100)
    limiter = WsLimiter(weighted, slot_budget=1)
    assert limiter.acquire("k1", ORG) and limiter.acquire("k2", ORG)  # 2 x 0.5 = 1 slot
    assert limiter.idle_slots() == 1
    assert not limiter.acquire("k3", ORG)  # ceil(1.5) = 2 > 1: refused, never under-counted
    limiter.release("k1", ORG)
    assert limiter.acquire("k3", ORG)
