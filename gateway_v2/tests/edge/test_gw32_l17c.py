"""GW17c offline forms of L17c-1..4 through the real MCP route."""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import replace

import pytest

httpx = pytest.importorskip("httpx")

import fakeredis  # noqa: E402

from gateway_v2.admit.mcp_cap import CallCapTable  # noqa: E402
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics  # noqa: E402
from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.domain.identity import Principal  # noqa: E402
from gateway_v2.edge.app import build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import TenantIdentity  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.resources_mcp import derive_cap_bounds  # noqa: E402
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    warm_snapshot_source,
)
from tests.edge._mcp_fixtures import ORG, Broker, Control, call, mcp_deps, server_row  # noqa: E402
from tests.edge.test_responses_route import _Clock, _contract, _Metrics, _none  # noqa: E402


class Identity:
    def __init__(self, principal: Principal) -> None:
        self._identity = TenantIdentity(ORG, principal)

    async def resolve(self, scope: object) -> TenantIdentity | None:
        del scope
        return self._identity


class Spend:
    """Counts budget evaluate/refund so a refund on a denial is observable."""

    def __init__(self) -> None:
        self.inner = AdmittingBudget()
        self.evaluated = self.refunded = 0

    async def evaluate(self, *args: object, **kwargs: object) -> object:
        self.evaluated += 1
        return await self.inner.evaluate(*args, **kwargs)  # type: ignore[arg-type]

    def refund(self, *args: object, **kwargs: object) -> None:
        self.refunded += 1
        self.inner.refund(*args, **kwargs)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


def _app(principal: Principal, *, cap: bool = True, reply: object | None = None):  # type: ignore[no-untyped-def]
    broker = Broker(reply)
    servers = [server_row("s")]
    tools = {"known_tools": ["a", "b"], "enabled_tools": ["a", "b"], "disabled_tools": []}
    pending: list[object] = []
    table = None
    if cap:
        table = CallCapTable(
            client=fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer()),
            keys=McpStoreKeys(),
            bounds=derive_cap_bounds(_contract()),
            clock=lambda: 0.0,
            rng=random.Random(1),
            metrics=McpPolicyMetrics(),
            spawn=lambda coro: pending.append(coro),
        )
    deps = replace(
        mcp_deps(broker, Control(servers, tools)),
        cap_table=table,
        policy_metrics=McpPolicyMetrics(),
    )
    budget = Spend()
    app = build_app(
        contract=_contract(),
        provider=None,
        scanner=holdback_scan,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=_Clock(),
        input_detector=_none,
        error_scanner=_none,
        identity=Identity(principal),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=budget,
        mcp=deps,  # type: ignore[arg-type]
    )
    return app, broker, budget, pending


async def _post(app: object, body: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        return await client.post(
            "/v1/mcp/s",
            content=json.dumps(body).encode(),
            headers={"authorization": "Bearer test", "content-type": "application/json"},
        )


def _code(resp: httpx.Response) -> str:
    return str(resp.json()["error"]["data"]["code"])


def _p(tools: frozenset[str] = frozenset(), cap: int = 0) -> Principal:
    return Principal("kid", ORG, 0.0, 0.0, 1, tools, cap)


_CALL_A = call("tools/call", {"name": "a", "arguments": {}})
_CALL_B = call("tools/call", {"name": "b", "arguments": {}})


def test_denied_tool_is_403_refunded_with_zero_broker_calls() -> None:
    app, broker, budget, _ = _app(_p(frozenset({"a"})))
    resp = asyncio.run(_post(app, _CALL_B))
    assert resp.status_code == 403 and _code(resp) == "mcp_tool_not_permitted"
    assert broker.requests == []
    assert budget.evaluated == 1 and budget.refunded == 1  # charged for admission, then refunded


def test_allowed_tool_reaches_the_broker() -> None:
    app, broker, _, _ = _app(_p(frozenset({"a"})))
    assert asyncio.run(_post(app, _CALL_A)).status_code == 200
    assert len(broker.requests) == 1


def test_tools_list_is_filtered_for_a_restricted_key() -> None:
    reply = {"jsonrpc": "2.0", "id": "x", "result": {"tools": [{"name": "a"}, {"name": "b"}]}}
    app, _, _, _ = _app(_p(frozenset({"a"})), reply=reply)
    tools = asyncio.run(_post(app, call("tools/list"))).json()["result"]["tools"]
    assert tools == [{"name": "a"}]


def test_deny_all_sentinel_blocks_everything_and_lists_empty() -> None:
    from gateway_v2.domain.identity import DENY_ALL_TOOLS

    reply = {"jsonrpc": "2.0", "id": "x", "result": {"tools": [{"name": "a"}]}}
    app, broker, _, _ = _app(_p(frozenset({DENY_ALL_TOOLS})), reply=reply)
    assert _code(asyncio.run(_post(app, _CALL_A))) == "mcp_tool_not_permitted"
    assert asyncio.run(_post(app, call("tools/list"))).json()["result"]["tools"] == []


def test_invalid_tool_name_is_invalid_params_for_a_restricted_key() -> None:
    app, broker, _, _ = _app(_p(frozenset({"a"})))
    resp = asyncio.run(_post(app, call("tools/call", {"name": "bad\x00", "arguments": {}})))
    assert _code(resp) == "mcp_invalid_params" and broker.requests == []


def test_cap_refuses_with_retry_after_and_refunds() -> None:
    app, broker, budget, pending = _app(_p(cap=1))
    first = asyncio.run(_post(app, _CALL_A))  # bootstrap allowance admits one cold call
    assert first.status_code == 200
    second = asyncio.run(_post(app, _CALL_A))
    assert second.status_code == 429 and _code(second) == "mcp_tool_call_cap_exceeded"
    assert int(second.headers["retry-after"]) >= 1
    assert second.headers["x-should-retry"] == "false"
    assert len(broker.requests) == 1
    assert budget.refunded == 1
    for coro in pending:
        coro.close()  # type: ignore[attr-defined]


def test_capped_key_without_a_cap_table_fails_closed() -> None:
    app, broker, _, _ = _app(_p(cap=5), cap=False)
    resp = asyncio.run(_post(app, _CALL_A))
    assert resp.status_code == 429 and broker.requests == []


def _norm_id(raw: bytes) -> bytes:
    return re.sub(rb"zs-[^\"]*?-[0-9a-f]{24}", b"zs-ID", raw)


def test_unrestricted_key_is_byte_identical_to_a_policy_free_run() -> None:
    plain, _, _, _ = _app(_p(), cap=False)
    with_table, _, _, _ = _app(_p(), cap=True)
    a = asyncio.run(_post(plain, _CALL_A))
    b = asyncio.run(_post(with_table, _CALL_A))
    assert a.status_code == b.status_code == 200
    # Byte-identical once the per-request id (random since request-id-uniqueness) is normalised.
    assert _norm_id(a.content) == _norm_id(b.content)  # the policy code added nothing


def test_negative_control_dropping_the_allow_list_call_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R22.5: with the allow-list call removed, the denied-tool assertion must fail."""
    import gateway_v2.edge.mcp as mcp_module

    monkeypatch.setattr(mcp_module, "apply_tool_policy", lambda *a, **k: None)
    app, broker, _, _ = _app(_p(frozenset({"a"})))
    resp = asyncio.run(_post(app, _CALL_B))
    with pytest.raises(AssertionError):
        assert resp.status_code == 403 and broker.requests == []
