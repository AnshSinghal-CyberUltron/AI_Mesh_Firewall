"""Property 5 through the real MCP route: the upstream credential reaches the broker body and no
other sink. Sinks inspected per iteration: every audit record (serialised bytes and repr), the
console block the audit carries, the metrics exposition, every log record, the client response
(body and headers) and the broker transport headers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import string
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")

from gateway_v2.audit.record import AuditRecord  # noqa: E402
from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.mcp_credentials import (  # noqa: E402
    ControlCredentialClient,
    CredentialCache,
    CredentialService,
)
from gateway_v2.edge import mcp as mcp_module  # noqa: E402
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
from tests.edge._mcp_fixtures import Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge._publisher_fixture import build_publisher  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _none  # noqa: E402

ITERATIONS = 120
_KINDS = ("bearer", "header", "basic")


def _secret(rng: random.Random) -> str:
    alphabet = string.ascii_letters + string.digits
    return "S" + "".join(rng.choice(alphabet) for _ in range(rng.randint(15, 40)))


def _cred_body(kind: str, secret: str) -> dict[str, object]:
    if kind == "header":
        return {"auth_type": "header", "token": secret, "header_name": "X-Api-Key", "version": 1}
    if kind == "basic":
        return {"auth_type": "basic", "token": secret, "username": "svc", "version": 1}
    return {"auth_type": "bearer", "token": secret, "version": 1}


def _run(kind: str, secret: str, reflect: bool, method: str):  # type: ignore[no-untyped-def]
    reply = {"jsonrpc": "2.0", "id": "x", "result": {"tools": []}}
    if reflect:
        reply["result"] = {"tools": [], "note": f"echo {secret}"}
    broker = Broker(reply)
    control_kind = "authheaders" if kind == "header" else kind
    cred_control = CredControl(_cred_body(kind, secret))
    service = CredentialService(
        client=ControlCredentialClient(
            base_url="https://control.example.com", internal_key="k", call=cred_control,
            bounds=BOUNDS,
        ),
        cache=CredentialCache(slots=8, ttl_s=120.0, clock=lambda: 0.0),
        lock=Lock(),
        token_endpoint=_endpoint(),
        bounds=BOUNDS,
        clock=lambda: 0.0,
    )
    deps = replace(
        mcp_deps(broker, Control([server_row("s", auth_type=control_kind)])),
        credentials=service,
        credential_bounds=BOUNDS,
    )
    records: list[AuditRecord] = []
    publisher = build_publisher(_contract(), lambda: 0.0)
    app = build_app(
        contract=_contract(), provider=None, scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(), metrics_registry=publisher, clock=_Clock(),
        input_detector=_none, error_scanner=_none, identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(), admission=AdmittingAdmission(),
        quota=AdmittingBudget(), mcp=deps, observer=publisher,
        audit=lambda record: records.append(record) or True,
    )
    body = call(method, {"name": "a", "arguments": {}} if method == "tools/call" else None)

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
            return await client.post(
                "/v1/mcp/s", content=json.dumps(body).encode(),
                headers={"authorization": "Bearer test", "content-type": "application/json"},
            )

    response = asyncio.run(go())
    return response, broker, records, publisher


def _sinks(response, broker, records, publisher, logs) -> list[str]:  # type: ignore[no-untyped-def]
    out = [response.text, json.dumps(dict(response.headers))]
    out += [json.dumps(req["headers"]) for req in broker.requests]  # broker transport headers
    for record in records:
        out += [record.serialize().decode("utf-8", "replace"), repr(record)]
        out.append(json.dumps(record.as_mapping(), default=str))
    out.append(publisher.exposition())
    out += [r.getMessage() for r in logs]
    return out


def _assert_property(secret: str, sinks: list[str]) -> None:
    for sink in sinks:
        assert secret not in sink
        assert secret[: BOUNDS.credential_min_len] not in sink


# Feature: gw32-mcp-transport, Property 5: a credential is in no sink (audit, console, metrics,
# logs, response, broker transport headers); it reaches only the broker's upstream headers.
def test_property_5_credential_in_no_sink(caplog: pytest.LogCaptureFixture) -> None:
    rng = random.Random(55)
    caplog.set_level(logging.DEBUG)
    for _ in range(ITERATIONS):
        secret = _secret(rng)
        kind = rng.choice(_KINDS)
        reflect = rng.random() < 0.3
        method = rng.choice(("tools/list", "tools/call"))
        caplog.clear()
        response, broker, records, publisher = _run(kind, secret, reflect, method)
        assert records, "the audit sink must be exercised for the property to mean anything"
        assert len(broker.requests) == 1
        upstream = json.dumps(broker.requests[0]["upstream"])
        assert secret in upstream or kind == "basic"  # basic is base64 in the header
        _assert_property(secret, _sinks(response, broker, records, publisher, caplog.records))
        if reflect:
            assert "output_blocked" in response.text


def test_property_5_negative_control_a_leaking_audit_detail_is_caught(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Patch the route's audit emit to carry the secret in `detail`; the property must go red."""
    secret = "Sleakleakleakleak1234"
    real = mcp_module.emit_chat_audit

    def leaking(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["detail"] = {**(kwargs.get("detail") or {}), "leak": secret}
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp_module, "emit_chat_audit", leaking)
    response, broker, records, publisher = _run("bearer", secret, False, "tools/list")
    with pytest.raises(AssertionError):
        _assert_property(secret, _sinks(response, broker, records, publisher, caplog.records))


def test_property_5_negative_control_a_leaking_log_line_is_caught(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "Sleakleakleakleak5678"
    caplog.set_level(logging.DEBUG)
    response, broker, records, publisher = _run("bearer", secret, False, "tools/list")
    logging.getLogger("gateway_v2.edge.mcp").warning("upstream token %s", secret)
    with pytest.raises(AssertionError):
        _assert_property(secret, _sinks(response, broker, records, publisher, caplog.records))
