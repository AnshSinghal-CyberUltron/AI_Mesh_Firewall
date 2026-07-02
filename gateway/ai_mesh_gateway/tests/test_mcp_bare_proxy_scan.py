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


@pytest.fixture(autouse=True)
def _force_direct_http_path(monkeypatch):
    # These tests mock the direct-httpx streamable-http upstream to exercise the
    # transport-agnostic scan/redaction pipeline. Pin the legacy direct path
    # (MCP_HTTP_VIA_SANDBOX off) so the mock is hit; the sandbox-routed path is
    # covered by the stdio adapter tests + test_mcp_http_via_sandbox.py.
    monkeypatch.setenv("MCP_HTTP_VIA_SANDBOX", "0")


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


def _auth(org_slug="demo", *, allowed=None, cap=0):
    payload = {
        "key_id": "k1",
        "user_id": 1,
        "project_id": "p1",
        "org_slug": org_slug,
        "mcp_allowed_tools": list(allowed or []),
        "mcp_max_tool_calls": cap,
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


# ── Per-key authorization parity on the bare REST route (CHG-0006, G2 item 3) ──


@pytest.mark.asyncio
async def test_rest_blocks_tool_not_in_key_allowlist():
    req = _rest_request(_auth(allowed=["other_tool"]), {"name": "fetch", "arguments": _BENIGN_ARG})
    backend = _http_resp({"result": [{"type": "text", "text": "ok"}]})
    fake = _fake_client([backend])
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 403
    data = _decode(resp)
    assert data["blocked"] is True and "not allowed" in data["detail"].lower()
    fake.post.assert_not_awaited()  # blocked BEFORE the backend is reached


@pytest.mark.asyncio
async def test_rest_blocks_over_tool_call_cap():
    req = _rest_request(_auth(cap=1), {"name": "fetch", "arguments": _BENIGN_ARG})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=2)),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 429
    assert _decode(resp)["blocked"] is True


@pytest.mark.asyncio
async def test_rest_blocks_disabled_tool():
    req = _rest_request(_auth(), {"name": "fetch", "arguments": _BENIGN_ARG})
    enabled = {"known": {"fetch"}, "enabled": set(), "disabled": {"fetch"}}
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=enabled)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 403
    assert "disabled" in _decode(resp)["detail"].lower()


@pytest.mark.asyncio
async def test_rest_allowed_tool_in_allowlist_passes():
    """An allowlisted tool (with a cap not exceeded) still flows to the backend."""
    req = _rest_request(_auth(allowed=["fetch"], cap=5),
                        {"name": "fetch", "arguments": _BENIGN_ARG})
    backend = _http_resp({"result": [{"type": "text", "text": "sunny in Paris"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 200
    assert "sunny in Paris" in json.dumps(_decode(resp))


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


# ════════════════════════════════════════════════════════════════════════════
# Fail-CLOSED on result-scan error (BACKSTOP CHG-0003, G2 item 2)
# The inbound arg scan already fails closed (arg_scan_error -> block). Prove the
# OUTBOUND result floor now does too: when the scanner errors, the RAW result is
# WITHHELD (blocked), never forwarded. Only the output scan is made to raise so
# the request reaches the result stage instead of being blocked at args.
# ════════════════════════════════════════════════════════════════════════════


def _output_scan_raises():
    """Patch target: _mcp_security_scan that succeeds on input, raises on output."""
    async def _side(payload, *, scan_direction, **_kw):
        if scan_direction == "output":
            raise RuntimeError("scanner unavailable")
        return payload, False, [], [], {}  # input: benign, no block
    return _side


@pytest.mark.asyncio
async def test_rest_result_scan_error_fails_closed():
    req = _rest_request(_auth(), {"name": "fetch", "arguments": _BENIGN_ARG})
    backend = _http_resp({"result": _PII_RESULT})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "_mcp_security_scan", side_effect=_output_scan_raises()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    blob = json.dumps(_decode(resp))
    # The un-inspectable result must be WITHHELD, never forwarded raw.
    assert "john.doe@example.com" not in blob
    data = _decode(resp)
    assert data.get("blocked") is True
    assert "SCAN_ERROR" in blob  # sentinel tag surfaces the fail-closed reason


@pytest.mark.asyncio
async def test_ext_result_scan_error_fails_closed():
    req = _ext_request({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 8,
                               "result": {"content": _PII_RESULT}})
    client = _ext_client(upstream)
    with (
        patch.object(mcp_proxy, "_mcp_security_scan", side_effect=_output_scan_raises()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob   # raw result withheld
    assert "error" in _decode(resp)             # returned as a JSON-RPC block error


@pytest.mark.asyncio
async def test_ext_redacts_pii_in_structured_content():
    """CHG-0005 (G2 item 2): PII in ``result.structuredContent`` (no ``content``
    key) is now scanned — the old branch only scanned dict ``result.content`` and
    let this shape egress raw."""
    req = _ext_request({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 11,
                               "result": {"structuredContent": {"owner": _PII_RESULT_TEXT}}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob   # structuredContent PII no longer egresses raw
    assert "j***@e***.com" in blob


@pytest.mark.asyncio
async def test_ext_redacts_pii_in_string_result():
    """A plain-string ``result`` (not a dict) is now scanned too."""
    req = _ext_request({"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 12, "result": _PII_RESULT_TEXT})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob
    assert "j***@e***.com" in blob


def _sse_resp(json_body, *, status=200):
    """A buffered SSE (text/event-stream) response stand-in: aread() yields one
    ``data:`` frame carrying ``json_body`` (mirrors an MCP tools/call SSE result)."""
    r = _ext_send_resp({}, content_type="text/event-stream", status=status)
    frame = f"data: {json.dumps(json_body)}\n\n".encode()
    r.aread = AsyncMock(return_value=frame)
    return r


@pytest.mark.asyncio
async def test_ext_sse_tool_result_redacted():
    """CHG-0004 (G2 item 2): a tools/call SSE result with PII is now BUFFERED,
    scanned, and re-emitted with the PII masked — no longer forwarded raw."""
    req = _ext_request({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    sse = _sse_resp({"jsonrpc": "2.0", "id": 7, "result": {"content": _PII_RESULT}})
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    body = bytes(resp.body).decode()
    assert "john.doe@example.com" not in body   # raw PII no longer egresses via SSE
    assert "j***@e***.com" in body              # masked form present in the re-emitted SSE
    assert resp.media_type == "text/event-stream"
    assert body.lstrip().startswith("data:")    # SSE framing preserved


@pytest.mark.asyncio
async def test_ext_sse_result_scan_error_fails_closed():
    """A scanner error on a tools/call SSE result WITHHOLDS it (fail-closed),
    never forwarding the raw frame."""
    req = _ext_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    sse = _sse_resp({"jsonrpc": "2.0", "id": 9, "result": {"content": _PII_RESULT}})
    client = _ext_client(sse)
    with (
        patch.object(mcp_proxy, "_mcp_security_scan", side_effect=_output_scan_raises()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "john.doe@example.com" not in blob   # withheld
    assert "error" in _decode(resp)             # returned as a JSON-RPC block error


@pytest.mark.asyncio
async def test_ext_non_toolscall_sse_passthrough(caplog):
    """Non-tools/call SSE (notifications / long-lived) is NOT buffered — it
    passes through live (there is no tool result to scan; buffering could hang)."""
    import logging

    req = _ext_request({"jsonrpc": "2.0", "id": 1, "method": "notifications/subscribe"})
    sse = _ext_send_resp({}, content_type="text/event-stream")

    async def _aiter():
        yield b"data: {\"jsonrpc\": \"2.0\", \"method\": \"notify\"}\n\n"

    sse.aiter_bytes = _aiter
    client = _ext_client(sse)
    with caplog.at_level(logging.WARNING):
        with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
            resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    assert any("streaming_egress_unscanned" in r.message for r in caplog.records)


# ── CHG-0033: ext_mcp_proxy must NOT forward the caller's gateway credentials to
# the third-party external MCP server (credential-leak / least-privilege). The
# outbound header set strips Authorization/Cookie/X-Api-Key + gateway-internal
# headers, and injects only the upstream's OWN stored OAuth token (if any).


def test_ext_proxy_forward_headers_strips_caller_credentials():
    inbound = {
        "Host": "gateway.internal",
        "Content-Length": "42",
        "Transfer-Encoding": "chunked",
        "Authorization": "Bearer caller-gateway-key",
        "Cookie": "session=supersecret",
        "X-Api-Key": "another-gw-key",
        "X-Gateway-User-Id": "1",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Mcp-Session-Id": "abc123",
    }
    out = mcp_proxy._ext_proxy_forward_headers(inbound)
    lower = {k.lower() for k in out}
    # credential / identity / hop-by-hop / gateway-internal headers all stripped
    assert "authorization" not in lower
    assert "cookie" not in lower
    assert "x-api-key" not in lower
    assert not any(k.startswith("x-gateway-") for k in lower)
    assert "host" not in lower and "content-length" not in lower and "transfer-encoding" not in lower
    # the caller's gateway key value must not survive anywhere
    assert "caller-gateway-key" not in json.dumps(out)
    assert "supersecret" not in json.dumps(out)
    # safe / protocol headers preserved
    assert out["Content-Type"] == "application/json"
    assert out["Accept"] == "text/event-stream"
    assert out["Mcp-Session-Id"] == "abc123"


def test_ext_proxy_forward_headers_injects_only_upstream_oauth():
    out = mcp_proxy._ext_proxy_forward_headers(
        {"Authorization": "Bearer caller-gateway-key", "Content-Type": "application/json"},
        oauth_token="upstream-oauth-token",
    )
    # the injected Authorization is the UPSTREAM's token, never the caller's key
    assert out["Authorization"] == "Bearer upstream-oauth-token"
    assert "caller-gateway-key" not in out["Authorization"]
