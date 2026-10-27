# Validates: Requirements 3.6, 7.1, 7.2, 7.3, 7.6, 10.1, 10.2, 10.3, 10.4
"""MCP registry, org map, MCP console object, and the MCP audit route."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

import pytest

from gateway_v2.audit.console_schema import validate_console
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.mcp_console import MCP_STAGES, mcp_console
from gateway_v2.edge.mcp_registry import McpRegistry, OrgMap
from gateway_v2.edge.request_holds import RequestHolds

_SERVERS = [
    {
        "server_slug": "search",
        "transport": "streamable-http",
        "url": "https://mcp.example.com/mcp",
        "is_active": True,
        "is_exposed_to_agents": True,
        "auth_type": "none",
        "oauth_authorized": False,
    },
    {"server_slug": "off", "is_active": False, "is_exposed_to_agents": True, "auth_type": "none"},
    {
        "server_slug": "keyed",
        "is_active": True,
        "is_exposed_to_agents": True,
        "auth_type": "bearer",
    },
    {"server_slug": "legacy", "is_active": True, "is_exposed_to_agents": True},
]
_TOOLS = {"known_tools": ["find", "read"], "enabled_tools": ["find"], "disabled_tools": ["read"]}


class _Control:
    def __init__(self, status: int = 200, fail: bool = False) -> None:
        self.status = status
        self.fail = fail
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], query: Mapping[str, str], timeout_s: float
    ) -> tuple[int, bytes]:
        self.calls.append((url, dict(headers), dict(query)))
        if self.fail:
            raise ConnectionError("down")
        body = _TOOLS if url.endswith("/enabled-tools/") else _SERVERS
        return self.status, json.dumps(body).encode()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _registry(control: _Control, clock: _Clock | None = None) -> McpRegistry:
    return McpRegistry(
        base_url="http://control:8100",
        internal_key="internal-key",
        transport=control,
        ttl_s=120.0,
        timeout_s=10.0,
        clock=clock or _Clock(),
    )


def test_server_lookup_sends_the_internal_headers() -> None:
    control = _Control()
    entry = asyncio.run(_registry(control).server("zeroshield", "search"))
    assert not isinstance(entry, str)
    assert entry.usable and not entry.needs_auth
    assert entry.url == "https://mcp.example.com/mcp"
    url, headers, _ = control.calls[0]
    assert url == "http://control:8100/api/mcp-connector/servers/"
    assert headers == {
        "X-Gateway-Auth": "true",
        "X-Gateway-Internal-Key": "internal-key",
        "X-Org-Slug": "zeroshield",
    }


def test_inactive_keyed_and_legacy_servers_fail_closed() -> None:
    registry = _registry(_Control())
    off = asyncio.run(registry.server("zeroshield", "off"))
    keyed = asyncio.run(registry.server("zeroshield", "keyed"))
    legacy = asyncio.run(registry.server("zeroshield", "legacy"))
    assert not isinstance(off, str) and not off.usable
    assert not isinstance(keyed, str) and keyed.needs_auth
    assert not isinstance(legacy, str) and legacy.needs_auth
    assert asyncio.run(registry.server("zeroshield", "missing")) == "mcp_server_not_found"


@pytest.mark.parametrize("control", (_Control(status=500), _Control(fail=True)))
def test_control_failure_is_unavailable_and_not_cached(control: _Control) -> None:
    registry = _registry(control)
    assert asyncio.run(registry.server("zeroshield", "search")) == "mcp_registry_unavailable"
    assert asyncio.run(registry.tools("zeroshield", "search")) == "mcp_registry_unavailable"
    assert asyncio.run(registry.server("zeroshield", "search")) == "mcp_registry_unavailable"
    assert len(control.calls) == 3


def test_ttl_hit_makes_no_second_fetch_and_expiry_refetches() -> None:
    control = _Control()
    clock = _Clock()
    registry = _registry(control, clock)
    asyncio.run(registry.server("zeroshield", "search"))
    asyncio.run(registry.server("zeroshield", "search"))
    assert len(control.calls) == 1
    clock.now += 121.0
    asyncio.run(registry.server("zeroshield", "search"))
    assert len(control.calls) == 2


def test_tool_set_and_headers() -> None:
    control = _Control()
    tools = asyncio.run(_registry(control).tools("zeroshield", "search"))
    assert not isinstance(tools, str)
    assert tools.allows("find")
    assert not tools.allows("read")
    assert not tools.allows("unknown")
    _, headers, query = control.calls[0]
    assert headers["X-Server-Slug"] == "search"
    assert query == {"server_slug": "search"}


def test_changed_scan_version_drops_the_entry() -> None:
    control = _Control()
    registry = _registry(control)
    versions = {"mcp:scan_ver:zeroshield": "1", "mcp:scan_ver:zeroshield:search": "4"}

    async def mget(keys: Sequence[str]) -> Sequence[object]:
        return [versions.get(key) for key in keys]

    async def run() -> None:
        await registry.server("zeroshield", "search")
        await registry.poll_once(mget)
        await registry.server("zeroshield", "search")
        assert len(control.calls) == 1
        versions["mcp:scan_ver:zeroshield:search"] = "5"
        await registry.poll_once(mget)
        await registry.server("zeroshield", "search")
        assert len(control.calls) == 2

    asyncio.run(run())


def test_org_map() -> None:
    org_map = OrgMap.parse("dev-org=zeroshield, other=acme")
    assert org_map.control_slug("dev-org") == "zeroshield"
    assert org_map.control_slug("missing") is None
    for raw in ("dev-org", "dev-org=-bad", "dev-org=has space", "=x"):
        with pytest.raises(ValueError):
            OrgMap.parse(raw)


def _check(console: dict[str, object]) -> dict[str, dict[str, object]]:
    validate_console(console)
    trace = console["pipeline_trace"]
    assert isinstance(trace, dict)
    stages = {stage["name"]: stage for stage in trace["stages"]}
    assert tuple(stages) == MCP_STAGES
    return stages


def test_console_ran_flags() -> None:
    allow = _check(mcp_console(request_id="r", plan_version="p", input_action="allow",
                               tool_called=True, output_action="allow"))
    assert all(stage["ran"] for stage in allow.values())
    blocked = _check(mcp_console(request_id="r", plan_version="p", input_action="block",
                                 input_rule="fw-x", tool_called=False, output_action=None))
    assert blocked["input_scan"]["deciding_rule"] == "fw-x"
    assert blocked["tool_call"] == {"name": "tool_call", "action": "skip", "ran": False}
    assert blocked["output_guardrail"]["ran"] is False
    broker_down = _check(mcp_console(request_id="r", plan_version="p", input_action="allow",
                                     tool_called=True, output_action=None))
    assert broker_down["tool_call"]["ran"] is True
    assert broker_down["output_guardrail"]["action"] == "skip"
    redacted = mcp_console(request_id="r", plan_version="p", input_action="allow",
                           tool_called=True, output_action="redact", output_rule="fw-pii")
    stages = _check(redacted)
    assert stages["output_guardrail"]["deciding_rule"] == "fw-pii"
    assert redacted["zeroshield"] == {"action": "redact", "request_id": "r"}


def test_audit_route_is_mcp_and_carries_no_text() -> None:
    holds = RequestHolds()
    holds.note_tenant("dev-org")
    holds.note_request("zs-mcp-1")
    holds.note_input(
        Decision(
            disposition=Disposition.ALLOW,
            per_finding=(),
            transformations=(),
            findings=(),
            plan_version="p1",
            deciding_rules=(),
            unavailable_detectors=(),
        )
    )
    records: list[object] = []
    console = mcp_console(request_id="zs-mcp-1", plan_version="p1", input_action="allow",
                          tool_called=True, output_action=None)
    emit_chat_audit(
        lambda record: records.append(record) or True,  # type: ignore[func-returns-value]
        holds,
        None,
        route="mcp",
        console=console,
        detail={"server_slug": "search", "tool": "find"},
    )
    assert len(records) == 1
    detail = records[0].detail  # type: ignore[attr-defined]
    assert detail["route"] == "mcp"
    assert detail["server_slug"] == "search" and detail["tool"] == "find"
    trace = detail["console"]["pipeline_trace"]
    assert [stage["name"] for stage in trace["stages"]] == list(MCP_STAGES)
