"""MCP scan-bypass parity for the three bare proxy paths.

The main JSON-RPC path (``org_mcp_jsonrpc``) already scans inbound tool ARGS
(credential hard-block) and outbound tool RESULTS (PII/secret redaction floor).
Three other routes historically forwarded verbatim with NO scan:

  1. ``org_mcp_tool_call``    — REST  POST /gateway/{org}/mcp/{server}/tools/call
  2. ``internal_tools_call``  — POST /v1/mcp/internal/tools-call (X-Gateway-Internal-Key)
  3. ``ext_mcp_proxy``        — transparent external proxy /v1/mcp/ext-proxy/{path}

These tests prove each path now (a) BLOCKS a credential in tool args and
(b) REDACTS PII in a tool result. The real two-tier scanner runs; only the
backend / upstream NETWORK is mocked (no sockets).

Run:
    cd gateway && PYTHONPATH=<repo>/shared:<repo>/gateway/ai_mesh_gateway:$PYTHONPATH \
        ./.venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Bootstrap import paths (mirrors test_e12_mcp_security.py exactly).
_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402


# A credential the real SECRET_PATTERNS reliably flags (token_assignment) so the
# orchestrator tags it threat_type="secret" → _findings_have_credential True.
_CRED_TEXT = "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"
_CRED_ARG = {"config": _CRED_TEXT}
_BENIGN_ARG = {"q": "what is the weather in Paris today"}
# An email the real detect_pii flags and redact_all masks → result floor fires.
_PII_RESULT_TEXT = "reach the owner at john.doe@example.com for access"
_PII_RESULT = [{"type": "text", "text": _PII_RESULT_TEXT}]


def _decode(resp):
    return json.loads(bytes(resp.body))


def _auth(org_slug="demo"):
    payload = {
        "key_id": "k1",
        "user_id": 1,
        "project_id": "p1",
        "org_slug": org_slug,
        "mcp_allowed_tools": [],
        "mcp_max_tool_calls": 0,
    }
    return AuthContext(key_hash="h" * 64, payload=payload)


def _fake_client(responses):
    """An httpx.AsyncClient stand-in whose .post() yields queued responses.

    ``responses`` is a list of objects returned in order across .post() calls.
    """
    seq = list(responses)

    async def _post(*_a, **_k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    client = AsyncMock()
    client.post = AsyncMock(side_effect=_post)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _http_resp(json_body, *, status=200, content_type="application/json"):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": content_type}
    r.raise_for_status = lambda: None
    return r


# ════════════════════════════════════════════════════════════════════════════
# Path 1 — org_mcp_tool_call (bare REST route)
# ════════════════════════════════════════════════════════════════════════════


def _rest_request(auth, body_obj):
    req = SimpleNamespace(state=SimpleNamespace(auth_context=auth))
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    return req


@pytest.mark.asyncio
async def test_rest_blocks_credential_in_args():
    req = _rest_request(_auth(), {"name": "search", "arguments": _CRED_ARG})
    # Backend would echo "ok" if reached — it must NOT be reached.
    backend = _http_resp({"result": [{"type": "text", "text": "ok"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 403
    data = _decode(resp)
    assert data["blocked"] is True
    assert "search" in data["detail"]


@pytest.mark.asyncio
async def test_rest_redacts_pii_in_result():
    req = _rest_request(_auth(), {"name": "fetch", "arguments": _BENIGN_ARG})
    backend = _http_resp({"result": _PII_RESULT})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 200
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob  # raw PII never egresses
    assert "j***@e***.com" in blob             # masked form present


@pytest.mark.asyncio
async def test_rest_benign_passes_through():
    req = _rest_request(_auth(), {"name": "fetch", "arguments": _BENIGN_ARG})
    backend = _http_resp({"result": [{"type": "text", "text": "sunny in Paris"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 200
    assert "sunny in Paris" in json.dumps(_decode(resp))


# ════════════════════════════════════════════════════════════════════════════
# Path 2 — internal_tools_call (X-Gateway-Internal-Key protected)
# ════════════════════════════════════════════════════════════════════════════


def _internal_request(body_obj):
    req = SimpleNamespace()
    req.headers = {"X-Gateway-Internal-Key": "ok"}
    req.json = AsyncMock(return_value=body_obj)
    return req


_SRV_CONFIG = {"transport": "streamable-http", "url": "https://safe.example.com/mcp"}


async def _run_internal(body_obj, *, upstream_call_json):
    """Drive internal_tools_call with control-plane + upstream stubbed.

    The upstream sees three POSTs (initialize, notifications/initialized,
    tools/call); only the third's body matters here.
    """
    init = _http_resp({"jsonrpc": "2.0", "id": 1, "result": {}})
    notif = _http_resp({})
    call = _http_resp(upstream_call_json)
    client = _fake_client([init, notif, call])
    req = _internal_request(body_obj)
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value=_SRV_CONFIG)),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        return await mcp_proxy.internal_tools_call(req), client


@pytest.mark.asyncio
async def test_internal_blocks_credential_in_args():
    body = {"org_slug": "demo", "server_slug": "srv",
            "tool_name": "search", "arguments": _CRED_ARG}
    resp, client = await _run_internal(
        body, upstream_call_json={"jsonrpc": "2.0", "id": 1,
                                  "result": {"content": [{"type": "text", "text": "ok"}]}})
    assert resp.status_code == 200
    data = _decode(resp)
    assert "error" in data
    assert "compliance tags" in data["error"]["message"]
    # Upstream tools/call must NOT have been issued (only init + notify = 2 posts).
    assert client.post.await_count <= 2


@pytest.mark.asyncio
async def test_internal_redacts_pii_in_result():
    body = {"org_slug": "demo", "server_slug": "srv",
            "tool_name": "fetch", "arguments": _BENIGN_ARG}
    resp, _ = await _run_internal(
        body, upstream_call_json={"jsonrpc": "2.0", "id": 1,
                                  "result": {"content": _PII_RESULT}})
    assert resp.status_code == 200
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob
    assert "j***@e***.com" in blob


# ════════════════════════════════════════════════════════════════════════════
# Path 3 — ext_mcp_proxy (transparent external proxy)
# ════════════════════════════════════════════════════════════════════════════


_EXT_HOST = next(iter(mcp_proxy._ALLOWED_MCP_DOMAINS))  # an allowlisted domain


def _ext_request(body_obj):
    req = SimpleNamespace()
    req.method = "POST"
    req.headers = {"content-type": "application/json"}
    req.query_params = {}
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    return req


def _ext_send_resp(json_body, *, content_type="application/json", status=200):
    """A streamed httpx response stand-in for ext_mcp_proxy (uses .send())."""
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.json = lambda: json_body
    r.aread = AsyncMock(return_value=json.dumps(json_body).encode())
    r.aclose = AsyncMock()
    return r


def _ext_client(send_resp):
    client = AsyncMock()
    client.build_request = lambda **_k: SimpleNamespace()
    client.send = AsyncMock(return_value=send_resp)
    client.aclose = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_ext_blocks_credential_in_args():
    req = _ext_request({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "search", "arguments": _CRED_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 5,
                               "result": {"content": [{"type": "text", "text": "ok"}]}})
    client = _ext_client(upstream)
    with (
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    data = _decode(resp)
    assert "error" in data and "compliance tags" in data["error"]["message"]
    # Upstream must NOT have been contacted — blocked before forwarding.
    client.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ext_redacts_pii_in_result():
    req = _ext_request({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 6,
                               "result": {"content": _PII_RESULT}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob
    assert "j***@e***.com" in blob


@pytest.mark.asyncio
async def test_ext_streaming_egress_unscanned_but_flagged(caplog):
    """SSE responses are NOT buffered/blocked (would break streaming) — instead
    a warning flags the unscanned egress. Inbound args were still scanned."""
    import logging

    req = _ext_request({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    sse = _ext_send_resp({}, content_type="text/event-stream")

    async def _aiter():
        yield b"data: {\"result\": 1}\n\n"

    sse.aiter_bytes = _aiter
    client = _ext_client(sse)
    with caplog.at_level(logging.WARNING):
        with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
            resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    # It still streams (does not 500 or buffer-block).
    assert resp.status_code == 200
    assert any("streaming_egress_unscanned" in r.message for r in caplog.records)
