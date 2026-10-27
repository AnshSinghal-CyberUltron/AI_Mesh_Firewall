"""Shared fakes for the GW17 MCP route tests: broker, control, DNS, detectors, app builder."""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence

import httpx

from gateway_v2.dispatch.mcp_broker import BrokerClient
from gateway_v2.domain import Category, Finding, FindingStatus, Span
from gateway_v2.edge.mcp_deps import McpDeps
from gateway_v2.edge.mcp_registry import McpRegistry, OrgMap
from tests.edge.test_responses_route import _app, _content, _Provider

ORG = "gw15-conformance-org"
CONTROL_SLUG = "acme"
STDIO_ALLOWED = frozenset({"npx", "node", "python", "python3", "uvx", "uv"})


def _finding(detector: str, start: int, end: int) -> Finding:
    return Finding(
        detector=detector,
        detector_version="mcp-test",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=start, end=end),),
        evidence=None,
    )


def detector(text: str) -> Sequence[Finding]:
    """``CARDX`` blocks (card class), ``MAILX`` is redacted (email class)."""
    found: list[Finding] = []
    for token, cls in (("CARDX", "card"), ("MAILX", "email")):
        index = text.find(token)
        while index >= 0:
            found.append(_finding(cls, index, index + len(token)))
            index = text.find(token, index + 1)
    if "SPLITA" + "SPLITB" in text:
        index = text.find("SPLITASPLITB")
        found.append(_finding("email", index, index + 12))
    return tuple(found)


class Broker:
    """Records each broker request and replies with a scripted envelope."""

    def __init__(self, reply: object | None = None, status: int = 200) -> None:
        self.reply = reply if reply is not None else {"jsonrpc": "2.0", "id": "x", "result": {}}
        self.status = status
        self.requests: list[dict[str, object]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float, max_bytes: int
    ) -> tuple[int, bytes]:
        self.requests.append({"url": url, "headers": dict(headers), **json.loads(body)})
        return self.status, json.dumps(self.reply).encode()


def server_row(slug: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "server_slug": slug,
        "transport": "streamable-http",
        "url": "https://mcp.example.com/mcp",
        "is_active": True,
        "is_exposed_to_agents": True,
        "auth_type": "none",
        "oauth_authorized": False,
    }
    row.update(overrides)
    return row


class Control:
    def __init__(self, servers: list[dict[str, object]], tools: dict[str, object] | None = None,
                 fail: bool = False) -> None:
        self.servers = servers
        self.tools = tools or {"known_tools": [], "enabled_tools": [], "disabled_tools": []}
        self.fail = fail
        self.calls = 0

    async def __call__(
        self, url: str, headers: Mapping[str, str], query: Mapping[str, str], timeout_s: float
    ) -> tuple[int, bytes]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("down")
        body = self.tools if url.endswith("/enabled-tools/") else self.servers
        return 200, json.dumps(body).encode()


DNS = {
    "mcp.example.com": ("93.184.216.34",),
    "meta.example.com": ("169.254.169.254",),
    "loop.example.com": ("127.0.0.1",),
}


async def resolve(host: str, port: int | None) -> Sequence[str]:
    del port
    if host not in DNS:
        raise OSError("NXDOMAIN")
    return DNS[host]


async def _no_sleep(_: float) -> None:
    return None


def mcp_deps(broker: Broker, control: Control, org_map: str = f"{ORG}={CONTROL_SLUG}") -> McpDeps:
    from tests.edge.test_responses_route import _contract

    bounds = _contract().mcp_bounds()
    return McpDeps(
        registry=McpRegistry(
            base_url="http://control:8100",
            internal_key="internal",
            transport=control,
            ttl_s=bounds.registry_ttl_s,
            timeout_s=bounds.registry_timeout_s,
            clock=lambda: 0.0,
        ),
        broker=BrokerClient(
            base_url="http://mcp-broker:8311",
            key="broker-key",
            bounds=bounds,
            transport=broker,
            rng=random.Random(1),
            sleep=_no_sleep,
        ),
        org_map=OrgMap.parse(org_map),
        dns=resolve,
        allow_hosts=frozenset(),
        stdio_allowed=STDIO_ALLOWED,
    )


def build(
    broker: Broker,
    control: Control,
    *,
    org_id: str | None = None,
    provider: _Provider | None = None,
    org_map: str = f"{ORG}={CONTROL_SLUG}",
) -> object:
    """The real ``build_app`` with chat's test wiring plus MCP dependencies."""
    return _app(
        provider or _Provider(_content()),
        input_detector=detector,
        error_scanner=detector,
        org_id=org_id,
        mcp=mcp_deps(broker, control, org_map),
    )


async def rpc(app: object, path: str, body: object) -> tuple[int, dict[str, object]]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        response = await client.post(
            path,
            content=raw,
            headers={"authorization": "Bearer test", "content-type": "application/json"},
        )
        decoded = json.loads(response.content) if response.content else {}
        assert isinstance(decoded, dict)
        return response.status_code, decoded


def call(method: str, params: object = None, msg_id: object = 1) -> dict[str, object]:
    body: dict[str, object] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return body
