"""MCP fails closed when the request id cannot be minted (request-id-uniqueness, 3.8)."""
from __future__ import annotations

import asyncio
import json

import pytest

from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import RequestIdUnavailable
from tests.edge._lgw29_app import ORG, Harness
from tests.edge._mcp_fixtures import Broker, Control, mcp_deps, server_row


def test_failing_mint_refuses_mcp_with_no_broker_call_admit_or_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(self: RequestHolds, org_id: str, *a: object, **k: object) -> str:
        raise RequestIdUnavailable()
    monkeypatch.setattr(RequestHolds, "begin_request", failing)
    broker = Broker()
    h = Harness(mcp=mcp_deps(broker, Control([server_row("search")])), org=ORG)
    gate_calls: list[object] = []
    original = h.rig.gate.decide_org
    def decide_org(*args: object, **kwargs: object) -> object:
        gate_calls.append(kwargs)
        return original(*args, **kwargs)  # type: ignore[arg-type]
    h.rig.gate.decide_org = decide_org  # type: ignore[method-assign,assignment]
    result = asyncio.run(
        h.call("/v1/mcp/search", {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    )
    body = json.loads(result.body)
    assert result.status == 503
    assert body["id"] == 7
    assert body["error"]["data"] == {"code": "request_id_unavailable", "request_id": None}
    assert "x-request-id" not in {k.lower() for k in result.headers}
    assert broker.requests == []
    assert gate_calls == []
    assert h.admission.admitted == 0
    assert h.budget.spent == 0 and h.budget.refunded == 0
