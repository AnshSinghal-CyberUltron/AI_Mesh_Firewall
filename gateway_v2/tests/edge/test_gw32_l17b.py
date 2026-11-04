"""GW17b offline forms of L17b-1..4: the credential reaches the broker body only."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.mcp_credentials import (  # noqa: E402
    ControlCredentialClient,
    CredentialCache,
    CredentialService,
)
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from tests.dispatch.test_gw32_credentials import BOUNDS, Lock, _endpoint  # noqa: E402
from tests.dispatch.test_gw32_credentials import Control as CredControl  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import Broker, Control, call, mcp_deps, rpc, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402

SECRET = "tok-LIVE-9f8e7d6c5b4a"


def _app(servers, cred_body, broker_reply=None, plaintext=frozenset()):  # type: ignore[no-untyped-def]
    broker = Broker(broker_reply)
    registry_control = Control(servers)
    cred_control = CredControl(cred_body)
    client = ControlCredentialClient(
        base_url="https://control.example.com", internal_key="k", call=cred_control, bounds=BOUNDS
    )
    service = CredentialService(
        client=client,
        cache=CredentialCache(slots=8, ttl_s=120.0, clock=lambda: 0.0),
        lock=Lock(),
        token_endpoint=_endpoint(),
        bounds=BOUNDS,
        clock=lambda: 0.0,
    )
    deps = replace(
        mcp_deps(broker, registry_control),
        credentials=service,
        credential_bounds=BOUNDS,
        plaintext_credential_hosts=plaintext,
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
    return app, broker, cred_control


_LIST = call("tools/list")
_BEARER = {"auth_type": "bearer", "token": SECRET, "version": 1}


def test_bearer_credential_reaches_only_the_broker_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app, broker, _ = _app([server_row("s", auth_type="bearer")], _BEARER)
    status, body = asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert status == 200
    sent = broker.requests[0]
    assert sent["upstream"]["headers"] == {"Authorization": f"Bearer {SECRET}"}  # type: ignore[index]
    assert SECRET not in json.dumps(body)
    assert SECRET not in json.dumps(sent["headers"])  # broker transport headers
    assert all(SECRET not in r.getMessage() for r in caplog.records)


def test_auth_none_body_is_unchanged() -> None:
    app, broker, cred = _app([server_row("s")], _BEARER)
    asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert broker.requests[0]["upstream"]["headers"] == {}  # type: ignore[index]
    assert cred.fetches == 0


def test_reflected_credential_is_withheld() -> None:
    reply = {"jsonrpc": "2.0", "id": "x", "result": {"tools": [], "note": f"saw {SECRET}"}}
    app, broker, _ = _app([server_row("s", auth_type="bearer")], _BEARER, reply)
    status, body = asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert body["error"]["data"]["code"] == "output_blocked"  # type: ignore[index]
    assert SECRET not in json.dumps(body)
    assert len(broker.requests) == 1


def test_plaintext_destination_refused_before_any_fetch() -> None:
    rows = [server_row("s", auth_type="bearer", url="http://mcp.example.com/m")]
    app, broker, cred = _app(rows, _BEARER)
    status, body = asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert body["error"]["data"]["code"] == "mcp_upstream_insecure_transport"  # type: ignore[index]
    assert broker.requests == [] and cred.fetches == 0


def test_stdio_server_with_credential_is_refused() -> None:
    rows = [server_row("s", auth_type="bearer", transport="stdio", url="", command="npx")]
    app, broker, cred = _app(rows, _BEARER)
    status, body = asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert body["error"]["data"]["code"] == "mcp_upstream_credential_invalid"  # type: ignore[index]
    assert broker.requests == [] and cred.fetches == 0


def test_short_secret_is_invalid_and_makes_no_broker_call() -> None:
    short = {"auth_type": "bearer", "token": "abc", "version": 1}
    app, broker, _ = _app([server_row("s", auth_type="bearer")], short)
    status, body = asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert (status, body["error"]["data"]["code"]) == (  # type: ignore[index]
        502,
        "mcp_upstream_credential_invalid",
    )
    assert broker.requests == []


def test_authheaders_maps_to_a_custom_header() -> None:
    cred = {"auth_type": "header", "token": SECRET, "header_name": "X-Api-Key", "version": 1}
    app, broker, _ = _app([server_row("s", auth_type="authheaders")], cred)
    asyncio.run(rpc(app, "/v1/mcp/s", _LIST))
    assert broker.requests[0]["upstream"]["headers"] == {"X-Api-Key": SECRET}  # type: ignore[index]
