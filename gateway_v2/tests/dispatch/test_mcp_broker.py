# Validates: Requirements 6.1, 6.2, 6.3, 6.6, 6.7, 6.8, 6.9
"""The MCP broker client: request shape, retry rule, error mapping."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping

from gateway_v2.dispatch.mcp_broker import (
    BrokerClient,
    BrokerNotSent,
    BrokerSent,
    BrokerTimeout,
    BrokerTooLarge,
    McpServer,
)
from gateway_v2.runtime.resources import McpBounds

_REMOTE = McpServer(slug="search", transport="streamable-http", url="https://mcp.example.com/mcp")
_STDIO = McpServer(
    slug="files", transport="stdio", command="npx", args=("-y", "pkg@1.0"), env={"A": "1"}
)
_OK = json.dumps({"jsonrpc": "2.0", "id": "x", "result": {"content": []}, "_meta": {"t": 1}})


class _Transport:
    """Plays back a script of statuses, bodies, or exceptions, and records each request."""

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.requests: list[tuple[str, dict[str, str], dict[str, object]]] = []

    async def __call__(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_s: float, max_bytes: int
    ) -> tuple[int, bytes]:
        self.requests.append((url, dict(headers), json.loads(body)))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, tuple)
        status, reply = step
        assert isinstance(status, int) and isinstance(reply, str)
        return status, reply.encode()


async def _no_sleep(_: float) -> None:
    return None


def _client(transport: _Transport) -> BrokerClient:
    return BrokerClient(
        base_url="http://mcp-broker:8311/",
        key="broker-key",
        bounds=McpBounds(),
        transport=transport,
        rng=random.Random(7),
        sleep=_no_sleep,
    )


def _call(transport: _Transport, server: McpServer = _REMOTE, method: str = "tools/list"):  # type: ignore[no-untyped-def]
    return asyncio.run(_client(transport).call("zeroshield", server, method, {"a": 1}, "zs-1"))


def test_remote_request_shape_and_headers() -> None:
    transport = _Transport([(200, _OK)])
    reply = _call(transport)
    assert reply.code is None
    url, headers, body = transport.requests[0]
    assert url == "http://mcp-broker:8311/v1/sandbox/zeroshield/rpc"
    assert headers["X-MCP-Broker-Key"] == "broker-key"
    assert headers["X-Request-ID"] == "zs-1"
    assert body["upstream"] == {
        "url": "https://mcp.example.com/mcp",
        "allowed_hosts": ["mcp.example.com"],
        "headers": {},
        "oauth_client_role": "forbidden_in_sandbox",
    }
    assert body["method"] == "tools/list" and body["params"] == {"a": 1}
    assert body["timeouts"] == {"init_seconds": 120.0, "method_seconds": 60.0}
    assert "stdio" not in body


def test_stdio_request_shape() -> None:
    transport = _Transport([(200, _OK)])
    _call(transport, _STDIO)
    body = transport.requests[0][2]
    assert body["stdio"] == {"command": "npx", "args": ["-y", "pkg@1.0"], "env": {"A": "1"}}
    assert "upstream" not in body


def test_meta_is_dropped() -> None:
    reply = _call(_Transport([(200, _OK)]))
    assert isinstance(reply.envelope, dict)
    assert "_meta" not in reply.envelope


def test_503_is_retried_then_succeeds() -> None:
    transport = _Transport([(503, "{}"), (200, _OK)])
    assert _call(transport).code is None
    assert len(transport.requests) == 2


def test_503_every_time_is_unavailable() -> None:
    transport = _Transport([(503, "{}")] * 5)
    assert _call(transport).code == "mcp_broker_unavailable"
    assert len(transport.requests) == McpBounds().broker_retry_max


def test_tools_call_is_not_retried_after_it_may_have_been_sent() -> None:
    transport = _Transport([BrokerSent("read error"), (200, _OK)])
    reply = _call(transport, method="tools/call")
    assert reply.code == "mcp_broker_unavailable"
    assert len(transport.requests) == 1


def test_tools_call_is_retried_when_never_sent() -> None:
    transport = _Transport([BrokerNotSent("connect"), (200, _OK)])
    assert _call(transport, method="tools/call").code is None
    assert len(transport.requests) == 2


def test_error_mapping() -> None:
    assert _call(_Transport([(502, "{}")])).code == "mcp_upstream_error"
    assert _call(_Transport([(401, "{}")])).code == "mcp_broker_unavailable"
    assert _call(_Transport([(429, "{}")])).code == "mcp_broker_unavailable"
    assert _call(_Transport([BrokerTimeout("slow")])).code == "mcp_broker_unavailable"
    assert _call(_Transport([BrokerTooLarge("big")])).code == "mcp_result_too_large"
    assert _call(_Transport([(200, "not json")])).code == "mcp_upstream_error"
    assert _call(_Transport([(200, '{"jsonrpc":"2.0"}')])).code == "mcp_upstream_error"
    assert _call(_Transport([RuntimeError("boom secret")])).code == "mcp_broker_unavailable"


def test_bad_slug_sends_nothing() -> None:
    transport = _Transport([(200, _OK)])
    reply = asyncio.run(_client(transport).call("-bad slug/", _REMOTE, "tools/list", {}, "zs-1"))
    assert reply.code == "mcp_broker_unavailable"
    assert transport.requests == []
