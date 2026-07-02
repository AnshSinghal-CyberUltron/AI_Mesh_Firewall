"""Unit tests for gateway → mcp-broker sandbox HTTP client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ai_mesh_gateway import mcp_sandbox_client as client


BROKER_KEY = "test-broker-secret"
ORG = "acme"
SERVER_CONFIG = {
    "server_slug": "playwright",
    "command": "npx",
    "args": ["-y", "@playwright/mcp@latest"],
    "env_vars": {"LINEAR_API_KEY": "lin_test"},
}


@pytest.fixture(autouse=True)
def _broker_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    monkeypatch.setenv("MCP_BROKER_URL", "http://broker.test:8311")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX", "3")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_BASE_DELAY", "0.01")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX_DELAY", "0.05")


def _response(
    status: int,
    *,
    json_body: dict | None = None,
    text: str = "",
    method: str = "POST",
    url: str = "http://broker.test:8311/v1/sandbox/acme/stdio/rpc",
) -> httpx.Response:
    return httpx.Response(
        status,
        json=json_body,
        text=text or ("" if json_body is None else httpx.Response(200, json=json_body).text),
        request=httpx.Request(method, url),
    )


@pytest.mark.asyncio
async def test_ensure_sandbox_success():
    body = {
        "org_slug": ORG,
        "status": "running",
        "container_id": "abc123",
        "agent_url": "http://172.28.0.42:9320",
    }
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(
        200,
        json_body=body,
        url=f"http://broker.test:8311/v1/sandbox/{ORG}/ensure",
    )
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        result = await client.ensure_sandbox(ORG)

    assert result == body
    call_kwargs = mock_http.request.call_args.kwargs
    assert call_kwargs["headers"][client.BROKER_KEY_HEADER] == BROKER_KEY
    assert call_kwargs["json"] == {"warm": True}


@pytest.mark.asyncio
async def test_ensure_sandbox_retries_503_then_succeeds():
    ok = _response(
        200,
        json_body={"org_slug": ORG, "status": "running"},
        url=f"http://broker.test:8311/v1/sandbox/{ORG}/ensure",
    )
    busy = _response(
        503,
        json_body={"detail": "Docker unavailable"},
        url=f"http://broker.test:8311/v1/sandbox/{ORG}/ensure",
    )
    mock_http = AsyncMock()
    mock_http.request.side_effect = [busy, busy, ok]
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        result = await client.ensure_sandbox(ORG)

    assert result["status"] == "running"
    assert mock_http.request.call_count == 3


@pytest.mark.asyncio
async def test_ensure_sandbox_polls_while_provisioning():
    # B3 #20: ensure re-polls while the broker reports provisioning=True, then
    # returns the ready result once the agent has bound.
    provisioning = _response(
        200,
        json_body={"org_slug": ORG, "status": "running", "provisioning": True, "agent_ready": False},
        url=f"http://broker.test:8311/v1/sandbox/{ORG}/ensure",
    )
    ready = _response(
        200,
        json_body={"org_slug": ORG, "status": "running", "provisioning": False, "agent_ready": True},
        url=f"http://broker.test:8311/v1/sandbox/{ORG}/ensure",
    )
    mock_http = AsyncMock()
    mock_http.request.side_effect = [provisioning, ready]
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        result = await client.ensure_sandbox(ORG)

    assert result["agent_ready"] is True
    assert result["provisioning"] is False
    assert mock_http.request.call_count == 2  # polled once while provisioning, then ready


@pytest.mark.asyncio
async def test_broker_send_jsonrpc_success():
    rpc_result = {"jsonrpc": "2.0", "id": 42, "result": {"tools": []}}
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(200, json_body=rpc_result)
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        result = await client.broker_send_jsonrpc(
            ORG,
            SERVER_CONFIG,
            "tools/list",
            None,
            timeout=30.0,
            msg_id=42,
        )

    assert result == rpc_result
    payload = mock_http.request.call_args.kwargs["json"]
    assert payload["server_slug"] == "playwright"
    assert payload["command"] == "npx"
    assert payload["method"] == "tools/list"
    assert payload["jsonrpc_id"] == 42
    assert payload["timeouts"]["method_seconds"] == 30.0
    assert payload["env"] == {"LINEAR_API_KEY": "lin_test"}
    assert mock_http.request.call_args.kwargs["headers"][client.BROKER_KEY_HEADER] == BROKER_KEY


@pytest.mark.asyncio
async def test_broker_send_jsonrpc_retries_503():
    ok = _response(200, json_body={"jsonrpc": "2.0", "id": 1, "result": {}})
    busy = _response(503, json_body={"detail": "Sandbox not running"})
    mock_http = AsyncMock()
    mock_http.request.side_effect = [busy, ok]
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        result = await client.broker_send_jsonrpc(
            ORG,
            SERVER_CONFIG,
            "tools/list",
            None,
        )

    assert result["result"] == {}
    assert mock_http.request.call_count == 2


@pytest.mark.asyncio
async def test_broker_send_jsonrpc_502_safe_error():
    detail = "Sandbox agent unreachable: connection refused at http://172.28.0.42:9320/rpc"
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(
        502,
        json_body={"detail": detail},
        text=detail,
    )
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        with pytest.raises(RuntimeError, match="temporarily unavailable") as exc:
            await client.broker_send_jsonrpc(
                ORG,
                SERVER_CONFIG,
                "tools/call",
                {"name": "x", "arguments": {}},
            )

    assert "172.28" not in str(exc.value)
    assert "connection refused" not in str(exc.value)


@pytest.mark.asyncio
async def test_missing_broker_key_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_BROKER_INTERNAL_KEY", raising=False)

    with pytest.raises(RuntimeError, match="not configured"):
        await client.ensure_sandbox(ORG)


@pytest.mark.asyncio
async def test_broker_send_requires_server_slug():
    bad_config = dict(SERVER_CONFIG)
    bad_config.pop("server_slug")

    with pytest.raises(ValueError, match="server_slug"):
        await client.broker_send_jsonrpc(ORG, bad_config, "tools/list", None)


@pytest.mark.asyncio
async def test_broker_send_rpc_stdio_routes_to_unified_endpoint():
    # P4.13/P6.18 §2: stdio goes through the unified /rpc route with transport=stdio
    # and legacy flat command/args/env for the agent.
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(200, json_body={"jsonrpc": "2.0", "id": 1, "result": {}})
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        await client.broker_send_rpc(ORG, SERVER_CONFIG, "tools/list", None)

    args, kwargs = mock_http.request.call_args
    assert args[1].endswith(f"/v1/sandbox/{ORG}/rpc")  # unified route
    body = kwargs["json"]
    assert body["transport"] == "stdio"
    assert body["command"] == SERVER_CONFIG["command"]
    assert "upstream" not in body


@pytest.mark.asyncio
async def test_broker_send_rpc_remote_builds_upstream_and_injects_bearer():
    # A remote transport (streamable-http) must NOT be dialed by the gateway: the
    # payload carries an `upstream` block (url + egress allowlist + injected Bearer)
    # so the sandbox agent does the dial. The gateway route is still …/rpc.
    remote_config = {
        "server_slug": "remote-http",
        "transport": "streamable-http",
        "url": "https://mcp.linear.app/mcp",
        "allowed_hosts": ["extra.example.com"],
    }
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(200, json_body={"jsonrpc": "2.0", "id": 1, "result": {}})
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        await client.broker_send_rpc(
            ORG, remote_config, "tools/list", None, oauth_token="tok-123"
        )

    args, kwargs = mock_http.request.call_args
    assert args[1].endswith(f"/v1/sandbox/{ORG}/rpc")
    body = kwargs["json"]
    assert body["transport"] == "streamable-http"
    up = body["upstream"]
    assert up["url"] == "https://mcp.linear.app/mcp"
    assert up["headers"]["Authorization"] == "Bearer tok-123"
    assert "mcp.linear.app" in up["allowed_hosts"]  # upstream host allowlisted
    assert "extra.example.com" in up["allowed_hosts"]  # declared extra host
    assert up["oauth_client_role"] == "forbidden_in_sandbox"
    # gateway sent NO direct upstream dial — only the broker route was called
    assert mock_http.request.call_count == 1


@pytest.mark.asyncio
async def test_broker_send_rpc_remote_requires_url():
    with pytest.raises(ValueError, match="url"):
        await client.broker_send_rpc(
            ORG, {"server_slug": "x", "transport": "sse"}, "tools/list", None
        )


# ── CHG-0051: propagate the request correlation id to the broker as X-Request-ID ──


@pytest.mark.asyncio
async def test_broker_send_rpc_propagates_correlation_id_as_x_request_id():
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(200, json_body={"result": {"ok": True}})
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None
    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        await client.broker_send_rpc(
            ORG, SERVER_CONFIG, "tools/call", {"name": "x"},
            msg_id=7, correlation_id="trace-xyz-123",
        )
    hdrs = mock_http.request.call_args.kwargs["headers"]
    assert hdrs["X-Request-ID"] == "trace-xyz-123"          # correlation id reaches the broker
    assert hdrs[client.BROKER_KEY_HEADER] == BROKER_KEY     # broker auth header still present


@pytest.mark.asyncio
async def test_broker_send_rpc_no_correlation_id_sends_no_x_request_id():
    mock_http = AsyncMock()
    mock_http.request.return_value = _response(200, json_body={"result": {"ok": True}})
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None
    with patch.object(client.httpx, "AsyncClient", return_value=mock_http):
        await client.broker_send_rpc(ORG, SERVER_CONFIG, "tools/call", {"name": "x"}, msg_id=7)
    hdrs = mock_http.request.call_args.kwargs["headers"]
    assert "X-Request-ID" not in hdrs                       # no empty/None header when unset
    assert hdrs[client.BROKER_KEY_HEADER] == BROKER_KEY
