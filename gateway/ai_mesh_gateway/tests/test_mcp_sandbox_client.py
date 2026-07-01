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
