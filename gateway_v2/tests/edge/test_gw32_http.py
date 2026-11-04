"""GW32 HTTP transport through the real route: paths, org check, sessions, SSE, GET, DELETE."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.admit.mcp_sessions import MemorySessionStore  # noqa: E402
from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.wire.mcp_sse import decode_events  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.resources_mcp import derive_transport_bounds  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

_TIME = [1000.0]


def _bounds(**over: float):  # type: ignore[no-untyped-def]
    return replace(derive_transport_bounds(_contract()), **over)


def _app(*, sessions: bool = True, bounds=None, origins=frozenset()):  # type: ignore[no-untyped-def]
    broker = Broker()
    tb = bounds or _bounds()
    store = MemorySessionStore(tb, lambda: _TIME[0]) if sessions else None
    deps = replace(
        mcp_deps(broker, Control([server_row("s")])),
        sessions=store,
        allowed_origins=origins,
        transport_bounds=tb,
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
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        mcp=deps,
    )
    return app, broker


async def _req(app, method, path, body=None, **headers):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    hdrs = {"authorization": "Bearer test", "host": "gw.test", **headers}
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
        content = None if body is None else json.dumps(body).encode()
        return await client.request(method, path, content=content, headers=hdrs)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


INIT = call("initialize", {"protocolVersion": "2025-03-26"})


def test_v1_and_gateway_paths_serve_the_same_handler() -> None:
    app, broker = _app()
    a = run(_req(app, "POST", "/v1/mcp/s", call("tools/list")))
    b = run(_req(app, "POST", "/gateway/acme/mcp/s", call("tools/list")))
    assert a.status_code == b.status_code == 200 and len(broker.requests) == 2


def test_org_mismatch_is_403_and_same_for_unknown_org() -> None:
    app, broker = _app()
    for org in ("other", "nosuchorg"):
        resp = run(_req(app, "POST", f"/gateway/{org}/mcp/s", call("tools/list")))
        assert resp.status_code == 403
        assert resp.json()["error"]["data"]["code"] == "mcp_org_mismatch"
    assert broker.requests == []


@pytest.mark.parametrize(
    "path",
    ["/gateway/acme/mcp/s/tools", "/gateway/acme/mcp/s/tools/call", "/gateway/acme/mcp/s/health"],
)
def test_non_mcp_gateway_shapes_are_the_routing_miss(path: str) -> None:
    app, broker = _app()
    resp = run(_req(app, "POST", path, call("tools/list")))
    from gateway_v2.edge import errors

    expected = errors.render_http("endpoint_not_found")
    assert resp.status_code == expected.status and resp.content == expected.body
    assert broker.requests == []


def test_initialize_opens_a_session_that_later_requests_may_present() -> None:
    app, broker = _app()
    init = run(_req(app, "POST", "/v1/mcp/s", INIT))
    sid = init.headers["mcp-session-id"]
    ok = run(_req(app, "POST", "/v1/mcp/s", call("tools/list"), **{"mcp-session-id": sid}))
    assert ok.status_code == 200
    bad = run(_req(app, "POST", "/v1/mcp/s", call("tools/list"), **{"mcp-session-id": "nope"}))
    assert bad.status_code == 404 and bad.json()["error"]["data"]["code"] == "mcp_session_invalid"
    other_server = run(_req(app, "POST", "/v1/mcp/t", call("ping"), **{"mcp-session-id": sid}))
    assert other_server.status_code == 404  # bound to server `s`
    assert len(broker.requests) == 1  # only the one valid tools/list reached the broker


def test_delete_ends_the_session() -> None:
    app, _ = _app()
    sid = run(_req(app, "POST", "/v1/mcp/s", INIT)).headers["mcp-session-id"]
    assert run(_req(app, "DELETE", "/v1/mcp/s", **{"mcp-session-id": sid})).status_code == 204
    assert run(_req(app, "DELETE", "/v1/mcp/s", **{"mcp-session-id": sid})).status_code == 404
    assert (
        run(_req(app, "POST", "/v1/mcp/s", call("ping"), **{"mcp-session-id": sid})).status_code
        == 404
    )


def test_session_limit_is_429_with_retry_after() -> None:
    app, _ = _app(bounds=_bounds(sessions_per_key=1))
    assert run(_req(app, "POST", "/v1/mcp/s", INIT)).status_code == 200
    second = run(_req(app, "POST", "/v1/mcp/s", INIT))
    assert second.status_code == 429 and int(second.headers["retry-after"]) >= 1
    assert second.json()["error"]["data"]["code"] == "mcp_session_limit"


def test_no_session_store_means_no_id_and_a_presented_id_is_invalid() -> None:
    app, _ = _app(sessions=False)
    init = run(_req(app, "POST", "/v1/mcp/s", INIT))
    assert init.status_code == 200 and "mcp-session-id" not in init.headers
    assert (
        run(_req(app, "POST", "/v1/mcp/s", call("ping"), **{"mcp-session-id": "x"})).status_code
        == 404
    )


def test_origin_and_protocol_version_gates() -> None:
    app, broker = _app(origins=frozenset({"https://app.example.com"}))
    evil = run(_req(app, "POST", "/v1/mcp/s", call("ping"), origin="https://evil.example.com"))
    assert evil.status_code == 403 and evil.json()["error"]["data"]["code"] == "mcp_origin_refused"
    same = run(_req(app, "POST", "/v1/mcp/s", call("ping"), origin="http://gw.test"))
    listed = run(_req(app, "POST", "/v1/mcp/s", call("ping"), origin="https://app.example.com"))
    assert same.status_code == listed.status_code == 200
    old = run(
        _req(app, "POST", "/v1/mcp/s", call("ping"), **{"mcp-protocol-version": "1999-01-01"})
    )
    assert old.status_code == 400
    assert old.json()["error"]["data"]["code"] == "mcp_transport_unsupported"


def test_sse_reply_is_one_message_event_with_the_same_body() -> None:
    app, _ = _app()
    plain = run(_req(app, "POST", "/v1/mcp/s", call("ping", msg_id=7)))
    sse = run(_req(app, "POST", "/v1/mcp/s", call("ping", msg_id=7), accept="text/event-stream"))
    assert sse.headers["content-type"].startswith("text/event-stream")
    ((event, data),) = decode_events(sse.content)
    assert event == "message" and json.loads(data) == plain.json()


def _drive_get(app, headers, wait_s=0.0):  # type: ignore[no-untyped-def]
    """Run a GET against the ASGI app directly; return (events sent, disconnect after wait_s)."""
    sent: list[dict[str, object]] = []

    async def go() -> None:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def receive() -> dict[str, object]:
            return await queue.get()

        async def send(event: dict[str, object]) -> None:
            sent.append(event)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/mcp/s",
            "raw_path": b"/v1/mcp/s",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
        task = asyncio.ensure_future(app(scope, receive, send))
        if wait_s:
            await asyncio.sleep(wait_s)
            await queue.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 5)

    asyncio.run(go())
    return sent


def test_get_requires_a_session_and_event_stream_accept() -> None:
    app, _ = _app(
        bounds=_bounds(stream_keepalive_s=0.01, stream_recheck_s=0.02, stream_max_lifetime_s=0.1)
    )
    base = {"authorization": "Bearer test", "host": "gw.test"}
    no_accept = _drive_get(app, {**base, "accept": "application/json"})
    assert no_accept[0]["status"] == 405
    no_session = _drive_get(app, {**base, "accept": "text/event-stream"})
    assert no_session[0]["status"] == 404


def test_get_stream_keeps_alive_then_stops_on_disconnect_and_at_max_lifetime() -> None:
    bounds = _bounds(stream_keepalive_s=0.01, stream_recheck_s=0.02, stream_max_lifetime_s=0.15)
    app, _ = _app(bounds=bounds)
    sid = run(_req(app, "POST", "/v1/mcp/s", INIT)).headers["mcp-session-id"]
    headers = {
        "authorization": "Bearer test",
        "host": "gw.test",
        "accept": "text/event-stream",
        "mcp-session-id": sid,
    }
    cut = _drive_get(app, headers, wait_s=0.05)  # client disconnects
    assert cut[0]["status"] == 200
    assert any(b"keepalive" in e.get("body", b"") for e in cut[1:])  # type: ignore[operator]
    assert cut[-1].get("more_body") is False
    timed = _drive_get(app, headers)  # no disconnect: the max lifetime ends it
    assert timed[-1].get("more_body") is False
