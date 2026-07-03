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


@pytest.fixture(autouse=True)
def _ssrf_guard_allows_by_default(monkeypatch):
    # CHG-0065 added an SSRF guard to ext_mcp_proxy that RESOLVES the target host via
    # getaddrinfo. These redaction/egress tests use real allowlisted domains and must
    # stay hermetic (no real DNS), so default the guard to "allow"; the dedicated SSRF
    # tests below override this to exercise the block path.
    monkeypatch.setattr(mcp_proxy, "is_safe_outbound_url", lambda *_a, **_k: (True, "ok"))


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


def _aiter_bytes_of(payload: bytes):
    """A real async-generator factory matching httpx.Response.aiter_bytes (CHG-0064:
    ext_mcp_proxy now buffers responses via _read_response_capped, which iterates
    aiter_bytes() rather than calling aread())."""
    async def _gen():
        yield payload
    return _gen


def _ext_send_resp(json_body, *, content_type="application/json", status=200):
    """A streamed httpx response stand-in for ext_mcp_proxy (uses .send())."""
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.json = lambda: json_body
    _payload = json.dumps(json_body).encode()
    r.aread = AsyncMock(return_value=_payload)
    r.aiter_bytes = _aiter_bytes_of(_payload)
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


# ── CHG-0118: completion/complete + resources/templates/list results carry
# server-controlled, model/user-facing content (autocompletion values / template
# metadata) and were forwarded RAW on the external proxy (not in
# _EXT_FINITE_RESULT_METHODS) — the same leak/tool-poisoning class as tools/list
# (CHG-0077) / initialize (CHG-0080). They are now scanned.


@pytest.mark.asyncio
async def test_ext_scans_completion_complete_values():
    req = _ext_request({"jsonrpc": "2.0", "id": 20, "method": "completion/complete",
                        "params": {"ref": {"type": "ref/prompt", "name": "x"},
                                   "argument": {"name": "a", "value": ""}}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 20, "result": {
        "completion": {"values": ["key AKIAIOSFODNN7EXAMPLE", "contact bob@corp.example"],
                       "total": 2}}})
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob   # secret in a suggested value no longer egresses raw
    assert "bob@corp.example" not in blob


@pytest.mark.asyncio
async def test_ext_scans_resources_templates_list_metadata():
    req = _ext_request({"jsonrpc": "2.0", "id": 21, "method": "resources/templates/list",
                        "params": {}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 21, "result": {
        "resourceTemplates": [{"name": "t", "uriTemplate": "file:///{path}",
                               "description": "admin key AKIAIOSFODNN7EXAMPLE host 10.9.8.7"}]}})
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob   # template description metadata scanned
    assert "10.9.8.7" not in blob


@pytest.mark.asyncio
async def test_ext_benign_completion_preserved():
    req = _ext_request({"jsonrpc": "2.0", "id": 22, "method": "completion/complete",
                        "params": {"ref": {"type": "ref/prompt", "name": "x"},
                                   "argument": {"name": "a", "value": "get"}}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 22, "result": {
        "completion": {"values": ["get_weather", "get_time"], "total": 2}}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "get_weather" in blob and "get_time" in blob  # benign suggestions untouched
    client.send.assert_awaited()  # benign input WAS forwarded to the upstream


# ── CHG-0119: input-side twin of CHG-0118 — credential/PII scan the completion/complete
# CLIENT INPUT (params.argument.value + params.context.arguments) before egress to the
# untrusted external server. A credential in the completion input is BLOCKED, never sent.


@pytest.mark.asyncio
async def test_ext_completion_input_credential_blocked_before_egress():
    req = _ext_request({"jsonrpc": "2.0", "id": 23, "method": "completion/complete",
                        "params": {"ref": {"type": "ref/prompt", "name": "x"},
                                   "argument": {"name": "a", "value": "my key AKIAIOSFODNN7EXAMPLE"}}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 23, "result": {"completion": {"values": []}}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "AKIAIOSFODNN7EXAMPLE" not in blob      # secret not echoed
    assert "compliance tags" in blob               # blocked with a compliance error
    client.send.assert_not_awaited()               # never egressed to the external server


@pytest.mark.asyncio
async def test_ext_completion_context_arguments_credential_blocked():
    req = _ext_request({"jsonrpc": "2.0", "id": 24, "method": "completion/complete",
                        "params": {"ref": {"type": "ref/prompt", "name": "x"},
                                   "argument": {"name": "a", "value": "hi"},
                                   "context": {"arguments": {"prev": "token AKIAIOSFODNN7EXAMPLE"}}}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 24, "result": {"completion": {"values": []}}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(_decode(resp))
    client.send.assert_not_awaited()


def _sse_resp(json_body, *, status=200):
    """A buffered SSE (text/event-stream) response stand-in: aread() yields one
    ``data:`` frame carrying ``json_body`` (mirrors an MCP tools/call SSE result)."""
    r = _ext_send_resp({}, content_type="text/event-stream", status=status)
    frame = f"data: {json.dumps(json_body)}\n\n".encode()
    r.aread = AsyncMock(return_value=frame)
    r.aiter_bytes = _aiter_bytes_of(frame)  # CHG-0064: SSE buffered via aiter_bytes now
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
async def test_ext_finite_sse_scans_interleaved_notification():
    """CHG-0122: a server-pushed notification frame (notifications/message /
    progress) carrying a secret, INTERLEAVED before the final result in a finite
    tools/call SSE, must be scanned — it was forwarded RAW (scan_notifications
    defaulted False on the finite branch, while the non-finite stream already
    scanned notifications, CHG-0098)."""
    req = _ext_request({"jsonrpc": "2.0", "id": 30, "method": "tools/call",
                        "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    frames = (
        'data: {"jsonrpc":"2.0","method":"notifications/message",'
        '"params":{"data":"key AKIAIOSFODNN7EXAMPLE email bob@corp.example"}}\n\n'
        'data: {"jsonrpc":"2.0","id":30,"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    ).encode()
    sse = _ext_send_resp({}, content_type="text/event-stream")
    sse.aread = AsyncMock(return_value=frames)
    sse.aiter_bytes = _aiter_bytes_of(frames)
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    body = bytes(resp.body).decode()
    assert "AKIAIOSFODNN7EXAMPLE" not in body   # interleaved notification secret masked
    assert "bob@corp.example" not in body
    assert "ok" in body                          # the actual result frame still delivered


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
async def test_ext_non_toolscall_sse_stream_scanned():
    """CHG-0098: a non-finite SSE stream (notifications / long-lived) is now SCANNED
    per event (bounded per-event buffering), NOT forwarded raw. A secret in a
    server notification is masked; a benign notification passes through. (Replaces the
    obsolete ``test_ext_non_toolscall_sse_passthrough`` which asserted the raw
    passthrough behavior CHG-0098 removed.)"""
    req = _ext_request({"jsonrpc": "2.0", "id": 1, "method": "notifications/subscribe"})
    sse = _ext_send_resp({}, content_type="text/event-stream")

    async def _aiter():
        yield (b'data: {"jsonrpc":"2.0","method":"notifications/message",'
               b'"params":{"data":"key AKIAIOSFODNN7EXAMPLE ok"}}\n\n')
        yield b'data: {"jsonrpc":"2.0","method":"notify","params":{"x":1}}\n\n'

    sse.aiter_bytes = _aiter
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    out = b""
    async for chunk in resp.body_iterator:
        out += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
    blob = out.decode("utf-8", "replace")
    assert "AKIAIOSFODNN7EXAMPLE" not in blob   # secret masked in the stream
    assert '"notify"' in blob                    # benign notification still passes through


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


# ── CHG-0110: least-privilege egress context-minimization. The outbound header set
# must also strip request-ROUTING / client-IDENTITY / topology headers so a third-party
# external MCP server never learns the caller's real IP, the internal gateway topology,
# or the internal URL + org/tenant slug (via referer).


def test_ext_proxy_forward_headers_strips_client_ip_and_topology():
    inbound = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Mcp-Session-Id": "sess-1",
        "Mcp-Protocol-Version": "2024-11-05",
        "User-Agent": "vscode",
        "X-Forwarded-For": "203.0.113.9, 10.0.0.2",
        "X-Real-IP": "203.0.113.9",
        "X-Forwarded-Host": "gw.internal.local",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Port": "443",
        "Forwarded": "for=203.0.113.9;host=gw.internal",
        "Via": "1.1 gw-internal",
        "Referer": "https://gw.internal/org/demo/chat",
    }
    out = mcp_proxy._ext_proxy_forward_headers(inbound)
    lower = {k.lower() for k in out}
    for h in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
              "x-forwarded-port", "x-real-ip", "forwarded", "via", "referer"):
        assert h not in lower, f"{h} leaked to third-party server"
    # none of the leaked VALUES survive anywhere (client IP, internal host, org slug)
    blob = json.dumps(out)
    assert "203.0.113.9" not in blob and "10.0.0.2" not in blob
    assert "gw.internal" not in blob
    assert "org/demo" not in blob  # tenant slug not disclosed via referer
    # protocol / benign headers still forwarded so the MCP handshake works
    assert out["Content-Type"] == "application/json"
    assert out["Mcp-Session-Id"] == "sess-1"
    assert out["Mcp-Protocol-Version"] == "2024-11-05"
    assert out["User-Agent"] == "vscode"


# ── CHG-0038: tools/list VISIBILITY parity with call-time authz. A restricted key
# must not even SEE tools it would be 403'd on at call time (_tool_allowed_by_key).


def test_filter_tools_by_key_allowlist_pure():
    from types import SimpleNamespace
    tools = [{"name": "echo"}, {"name": "get-sum"}, {"name": "delete-all"}]
    # empty allowlist -> all visible (mirrors _tool_allowed_by_key)
    assert mcp_proxy._filter_tools_by_key_allowlist(tools, SimpleNamespace(mcp_allowed_tools=[])) == tools
    # None auth -> unchanged
    assert mcp_proxy._filter_tools_by_key_allowlist(tools, None) == tools
    # restricted -> only allowed tools survive
    out = mcp_proxy._filter_tools_by_key_allowlist(tools, SimpleNamespace(mcp_allowed_tools=["echo", "get-sum"]))
    assert [t["name"] for t in out] == ["echo", "get-sum"]
    # non-dict / name-less entries pass through (same as _filter_tools_by_enabled)
    weird = [{"no_name": 1}, "raw", {"tool_name": "echo"}, {"tool_name": "nope"}]
    out2 = mcp_proxy._filter_tools_by_key_allowlist(weird, SimpleNamespace(mcp_allowed_tools=["echo"]))
    assert {"no_name": 1} in out2 and "raw" in out2 and {"tool_name": "echo"} in out2
    assert {"tool_name": "nope"} not in out2


@pytest.mark.asyncio
async def test_org_mcp_tools_list_hides_tools_outside_key_allowlist():
    from types import SimpleNamespace
    req = SimpleNamespace(state=SimpleNamespace(
        auth_context=SimpleNamespace(mcp_allowed_tools=["echo"], org_slug="demo")))
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: [{"tool_name": "echo"}, {"tool_name": "get-sum"}]
    client = AsyncMock()
    client.get = AsyncMock(return_value=backend_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch.object(mcp_proxy, "_validate_org_scope", return_value=None), \
         patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)), \
         patch.object(mcp_proxy, "_backend_proxy_headers", return_value={}), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.org_mcp_tools_list("demo", "srv", req)
    data = json.loads(resp.body.decode())
    names = [t.get("tool_name") or t.get("name") for t in data]
    assert names == ["echo"]  # get-sum hidden by the key allowlist


# ── CHG-0039: extend ext_mcp_proxy SSE result scanning beyond tools/call to the
# other FINITE request/response methods (resources/*, prompts/*). Previously a
# resources/read SSE result egressed RAW (only tools/call SSE was scanned).


@pytest.mark.asyncio
async def test_ext_sse_resources_read_result_now_scanned():
    req = _ext_request({"jsonrpc": "2.0", "id": 8, "method": "resources/read",
                        "params": {"uri": "file:///doc.txt"}})
    sse = _sse_resp({"jsonrpc": "2.0", "id": 8,
                     "result": {"contents": [{"uri": "file:///doc.txt",
                                              "text": "reach john.doe@example.com"}]}})
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    body = bytes(resp.body).decode()
    assert "john.doe@example.com" not in body    # resource PII no longer egresses raw via SSE
    assert resp.media_type == "text/event-stream"
    # it was BUFFERED (scanned), not streamed live — a buffered SSE returns a plain
    # Response, not a StreamingResponse (CHG-0064: buffering now iterates aiter_bytes()).
    from starlette.responses import StreamingResponse
    assert not isinstance(resp, StreamingResponse)


@pytest.mark.asyncio
async def test_ext_sse_notification_still_streams_through_unbuffered():
    """A non-finite method (notifications) is NOT buffered — it streams through
    live (buffering could hang a long-lived stream)."""
    from starlette.responses import StreamingResponse
    req = _ext_request({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
    sse = _ext_send_resp({}, content_type="text/event-stream")

    async def _aiter():
        yield b"data: {\"jsonrpc\":\"2.0\"}\n\n"

    sse.aiter_bytes = _aiter
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert isinstance(resp, StreamingResponse)     # pass-through, not buffered
    sse.aread.assert_not_awaited()                 # NOT buffered (no hang risk)


# ── CHG-0043 (was CHG-0040; renumbered — id collided w/ P4.13): a JSON-RPC ERROR
# response (no result) can leak a secret in its
# message from an untrusted external server — ext_mcp_proxy now scans error
# content too (non-streaming + SSE), not just the result.
_ERR_SECRET = "connect failed: postgres://admin:s3cr3tPass@db.internal/prod"


@pytest.mark.asyncio
async def test_ext_error_content_masked_non_streaming():
    req = _ext_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "db", "arguments": _BENIGN_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 3,
                               "error": {"code": -32000, "message": _ERR_SECRET}})
    client = _ext_client(upstream)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    blob = json.dumps(_decode(resp))
    assert "s3cr3tPass" not in blob                 # secret in the error message masked
    assert "error" in _decode(resp)                 # still an error response


@pytest.mark.asyncio
async def test_ext_sse_error_frame_masked():
    req = _ext_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "db", "arguments": _BENIGN_ARG}})
    sse = _sse_resp({"jsonrpc": "2.0", "id": 4, "error": {"code": -32000, "message": _ERR_SECRET}})
    client = _ext_client(sse)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    body = bytes(resp.body).decode()
    assert "s3cr3tPass" not in body                 # secret in the SSE error frame masked
    assert resp.media_type == "text/event-stream"


# ── CHG-0041: the ext-proxy inbound credential block now covers prompts/get args
# (same params.arguments shape as tools/call), not just tools/call — an accidental
# credential in prompt-template args must not egress to the external server.


@pytest.mark.asyncio
async def test_ext_blocks_credential_in_prompts_get_args():
    req = _ext_request({"jsonrpc": "2.0", "id": 6, "method": "prompts/get",
                        "params": {"name": "summarize", "arguments": _CRED_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 6, "result": {"messages": []}})
    client = _ext_client(upstream)
    with (
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    data = _decode(resp)
    assert "error" in data and "compliance tags" in data["error"]["message"]
    client.send.assert_not_awaited()               # blocked before forwarding upstream


# ── CHG-0050: request correlation id (X-Request-ID) on the bare REST audit path ──


@pytest.mark.asyncio
async def test_rest_audit_carries_x_request_id_correlation():
    # A bare-REST tool call must thread the inbound X-Request-ID into its audit event
    # (this route previously recorded audit events with NO correlation id at all), so
    # a block is traceable across gateway -> broker -> sandbox.
    req = _rest_request(_auth(allowed=["other_tool"]), {"name": "fetch", "arguments": _BENIGN_ARG})
    req.headers = {"x-request-id": "trace-xyz-123"}
    rec = AsyncMock()
    backend = _http_resp({"result": [{"type": "text", "text": "ok"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", rec),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client([backend])),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 403                          # "fetch" not in key allowlist -> block
    assert rec.await_count >= 1
    assert rec.await_args.kwargs.get("request_id") == "trace-xyz-123"


def test_correlation_id_prefers_x_request_id():
    req = SimpleNamespace(headers={"x-request-id": "trace-abc"})
    assert mcp_proxy._mcp_request_correlation_id(req, 5) == "trace-abc"


def test_correlation_id_falls_back_to_msg_id():
    assert mcp_proxy._mcp_request_correlation_id(SimpleNamespace(headers={}), 42) == "42"


def test_correlation_id_empty_when_neither():
    assert mcp_proxy._mcp_request_correlation_id(SimpleNamespace(headers={}), None) == ""


def test_correlation_id_bounds_hostile_header():
    req = SimpleNamespace(headers={"x-request-id": "x" * 500})
    assert len(mcp_proxy._mcp_request_correlation_id(req, None)) == 200


def test_correlation_id_survives_missing_headers_attr():
    # A request object with no `.headers` must not raise (internal call paths).
    assert mcp_proxy._mcp_request_correlation_id(SimpleNamespace(), 7) == "7"


# ════════════════════════════════════════════════════════════════════════════
# CHG-0061: ext_mcp_proxy egress leak on NON-200 and NON-JSON bodies.
# The outbound scan was gated on `status_code == 200` (JSON only), so a non-200
# JSON error body, a non-200 body without result/error, or ANY non-JSON text body
# egressed RAW/unscanned — an untrusted external server could leak a secret/PII/
# infra string. These prove all of those bodies are now scanned (fail-closed),
# and that binary bodies are still passed through untouched.
# ════════════════════════════════════════════════════════════════════════════
def _ext_send_text_resp(body_bytes, *, content_type="text/plain", status=200):
    """An httpx response stand-in whose .json() RAISES (non-JSON body)."""
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}

    def _raise_json():
        raise ValueError("not json")

    r.json = _raise_json
    r.aread = AsyncMock(return_value=body_bytes)
    r.aiter_bytes = _aiter_bytes_of(body_bytes)
    r.aclose = AsyncMock()
    return r


_BENIGN_CALL = {"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                "params": {"name": "fetch", "arguments": _BENIGN_ARG}}
_LEAK_EMAIL = "john.doe@example.com"


@pytest.mark.asyncio
async def test_ext_non200_json_error_body_redacted():
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_resp(
        {"jsonrpc": "2.0", "id": 20,
         "error": {"code": -32000, "message": f"connect failed; reach {_LEAK_EMAIL}"}},
        status=500,
    )
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 500                       # upstream status preserved
    assert _LEAK_EMAIL not in json.dumps(_decode(resp))  # email masked, not leaked


@pytest.mark.asyncio
async def test_ext_non200_json_detail_body_without_result_or_error_redacted():
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_resp({"detail": f"user {_LEAK_EMAIL} not found"}, status=404)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 404
    assert _LEAK_EMAIL not in json.dumps(_decode(resp))


@pytest.mark.asyncio
async def test_ext_non200_json_result_body_redacted():
    # the result branch must also run on a non-200 status
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_resp(
        {"jsonrpc": "2.0", "id": 20,
         "result": {"content": [{"type": "text", "text": f"owner {_LEAK_EMAIL}"}]}},
        status=502,
    )
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 502
    assert _LEAK_EMAIL not in json.dumps(_decode(resp))


@pytest.mark.asyncio
async def test_ext_non_json_text_body_redacted():
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_text_resp(
        f"Error page: reach owner at {_LEAK_EMAIL} for access".encode(),
        content_type="text/plain", status=500,
    )
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 500
    body_text = bytes(resp.body).decode("utf-8", errors="replace")
    assert _LEAK_EMAIL not in body_text                  # text body scanned + masked


@pytest.mark.asyncio
async def test_ext_binary_body_passed_through_untouched():
    # a binary (image) body must NOT be text-scanned (would corrupt it) — pass raw
    req = _ext_request(_BENIGN_CALL)
    raw = b"\x89PNG\r\n\x1a\n\x00\x01\x02rawbytes-not-text"
    upstream = _ext_send_text_resp(raw, content_type="image/png", status=200)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert bytes(resp.body) == raw                       # byte-for-byte unchanged


@pytest.mark.asyncio
async def test_ext_non_json_text_body_fails_closed_on_scan_error():
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_text_resp(
        f"reach {_LEAK_EMAIL}".encode(), content_type="text/plain", status=500)
    _real_scan = mcp_proxy._mcp_security_scan

    async def _fail_output_only(*a, **k):
        # raise ONLY on the outbound (result) scan; let the inbound arg scan run
        # really so the request reaches the response-body stage.
        if k.get("scan_direction") == "output":
            raise RuntimeError("boom")
        return await _real_scan(*a, **k)

    with (
        patch.object(mcp_proxy, "_mcp_security_scan", side_effect=_fail_output_only),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)),
    ):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    # scan error on a text body -> WITHHELD (fail closed), raw email never egresses
    body_text = bytes(resp.body).decode("utf-8", errors="replace")
    assert _LEAK_EMAIL not in body_text
    assert "withheld" in body_text.lower()


# ════════════════════════════════════════════════════════════════════════════
# CHG-0064: response-side memory-DoS. ext_mcp_proxy buffered an untrusted external
# server's whole response (resp.aread()) with NO size ceiling — a timeout bounds
# TIME, not SIZE, so a fast multi-GB response OOMs the shared gateway (cross-tenant
# DoS). The response read is now capped; an oversize upstream body -> 502.
# ════════════════════════════════════════════════════════════════════════════
def _ext_send_oversized_resp(chunk, *, content_type="application/json", status=200, n=10):
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.json = lambda: {}

    async def _big():
        for _ in range(n):
            yield chunk  # streamed in chunks so the cap fires mid-stream

    r.aiter_bytes = _big
    r.aread = AsyncMock(return_value=chunk * n)
    r.aclose = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_ext_oversized_json_response_returns_502():
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_oversized_resp(b"x" * 60, content_type="application/json")
    with patch.object(mcp_proxy, "_MCP_MAX_RESPONSE_BYTES", 100), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 502
    assert _decode(resp)["code"] == "mcp_upstream_response_too_large"


@pytest.mark.asyncio
async def test_ext_oversized_sse_response_returns_502():
    req = _ext_request(_BENIGN_CALL)  # tools/call -> finite SSE -> buffered+capped
    upstream = _ext_send_oversized_resp(
        b"data: " + b"x" * 60 + b"\n\n", content_type="text/event-stream")
    with patch.object(mcp_proxy, "_MCP_MAX_RESPONSE_BYTES", 100), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 502
    assert _decode(resp)["code"] == "mcp_upstream_response_too_large"


@pytest.mark.asyncio
async def test_ext_response_under_cap_unaffected():
    # a normal-sized response is untouched by the cap
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_resp(
        {"jsonrpc": "2.0", "id": 20, "result": {"content": [{"type": "text", "text": "ok"}]}})
    with patch.object(mcp_proxy, "_MCP_MAX_RESPONSE_BYTES", 10 * 1024 * 1024), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200


# ════════════════════════════════════════════════════════════════════════════
# CHG-0065: SSRF guard on ext_mcp_proxy. The domain allowlist matches the hostname
# STRING only — it does NOT catch an allowlisted domain that RESOLVES to an internal
# / loopback / link-local / cloud-metadata address (DNS rebinding / hijack / misconfig),
# which would let a caller reach internal services or 169.254.169.254 (metadata →
# credential theft). is_safe_outbound_url() resolves + blocks; ext_mcp_proxy now calls it.
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_ext_proxy_ssrf_guard_blocks_when_url_unsafe():
    # wiring: when the guard rejects the resolved target, ext_mcp_proxy returns 400
    req = _ext_request(_BENIGN_CALL)
    with patch.object(mcp_proxy, "is_safe_outbound_url",
                      lambda *_a, **_k: (False, "cloud metadata endpoint (169.254.169.254)")):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 400
    err = _decode(resp)["error"]
    assert "SSRF guard" in err and "metadata" in err


@pytest.mark.asyncio
async def test_ext_proxy_ssrf_guard_real_resolution_blocks_localhost(monkeypatch):
    # end-to-end with the REAL guard: an allowlisted host that resolves to loopback
    # (127.0.0.1) is blocked before any forward. localhost always resolves locally, so
    # this is deterministic without network.
    from _url_guard import is_safe_outbound_url as _real
    monkeypatch.delenv("MCP_ALLOW_INTERNAL_HOSTS", raising=False)
    req = _ext_request(_BENIGN_CALL)
    with patch.object(mcp_proxy, "_ALLOWED_MCP_DOMAINS", {"localhost"}), \
         patch.object(mcp_proxy, "is_safe_outbound_url", _real):
        resp = await mcp_proxy.ext_mcp_proxy("localhost/mcp", req)
    assert resp.status_code == 400
    assert "SSRF guard" in _decode(resp)["error"]


@pytest.mark.asyncio
async def test_ext_proxy_ssrf_guard_allows_safe_public_host():
    # a safe (public) resolved target is NOT SSRF-blocked — it proceeds to the normal
    # scan/forward path (mock upstream returns a benign result).
    req = _ext_request(_BENIGN_CALL)
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 20,
                               "result": {"content": [{"type": "text", "text": "ok"}]}})
    with patch.object(mcp_proxy, "is_safe_outbound_url", lambda *_a, **_k: (True, "ok")), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200  # not SSRF-blocked


# ── CHG-0068: ext_mcp_proxy must AUDIT its enforcement decisions (item 9). The
# external passthrough previously recorded NONE of its blocks/redactions, so external
# tool usage + thwarted attacks were invisible in the MCPEvent trail (every other MCP
# path audits via _record_gateway_event). These prove the block/redact events now record.
def _ext_request_with_org(body_obj, org_slug="demo"):
    req = _ext_request(body_obj)
    req.state = SimpleNamespace(auth_context=_auth(org_slug=org_slug))
    return req


@pytest.mark.asyncio
async def test_ext_proxy_audits_result_redaction():
    req = _ext_request_with_org(_BENIGN_CALL)
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 20, "result": {"content": _PII_RESULT}})
    events = []

    async def _cap(**kw):
        events.append(kw)

    with patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", new_callable=AsyncMock, return_value=None), \
         patch.object(mcp_proxy, "_record_gateway_event", new=_cap), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    redacts = [e for e in events if e.get("decision") == "redact"]
    assert redacts, f"no redact audit event: {events}"
    assert redacts[0]["org_slug"] == "demo"
    assert str(redacts[0]["server_slug"]).startswith("ext:")


@pytest.mark.asyncio
async def test_ext_proxy_audits_ssrf_block():
    req = _ext_request_with_org(_BENIGN_CALL)
    events = []

    async def _cap(**kw):
        events.append(kw)

    with patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", new_callable=AsyncMock, return_value=None), \
         patch.object(mcp_proxy, "is_safe_outbound_url", lambda *_a, **_k: (False, "cloud metadata")), \
         patch.object(mcp_proxy, "_record_gateway_event", new=_cap):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 400
    assert any(e.get("decision") == "block" and e.get("reason") == "ssrf_blocked" for e in events), events


@pytest.mark.asyncio
async def test_ext_proxy_audits_credential_block():
    req = _ext_request_with_org({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "search", "arguments": _CRED_ARG}})
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 5,
                               "result": {"content": [{"type": "text", "text": "ok"}]}})
    events = []

    async def _cap(**kw):
        events.append(kw)

    with patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", new_callable=AsyncMock, return_value=None), \
         patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True), \
         patch.object(mcp_proxy, "_record_gateway_event", new=_cap), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert any(e.get("decision") == "block" and e.get("reason") == "credential_blocked_inbound"
               for e in events), events


@pytest.mark.asyncio
async def test_ext_proxy_audit_noop_without_org():
    # unauthenticated (no org) → _record_gateway_event returns early; audit is a safe no-op
    req = _ext_request(_BENIGN_CALL)  # no .state → no org
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 20, "result": {"content": _PII_RESULT}})
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200  # still works, redaction still applied
    assert "john.doe@example.com" not in json.dumps(_decode(resp))


# ── CHG-0070: complete the ext_mcp_proxy audit trail — CHG-0068 audited the JSON result
# block/redact but MISSED the SSE result block, and no successful tool-call was audited.
@pytest.mark.asyncio
async def test_ext_proxy_audits_clean_tool_call_allow():
    req = _ext_request_with_org(_BENIGN_CALL)  # tools/call name=fetch
    upstream = _ext_send_resp({"jsonrpc": "2.0", "id": 20,
                               "result": {"content": [{"type": "text", "text": "ok"}]}})  # clean
    events = []

    async def _cap(**kw):
        events.append(kw)

    with patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", new_callable=AsyncMock, return_value=None), \
         patch.object(mcp_proxy, "_record_gateway_event", new=_cap), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert resp.status_code == 200
    allows = [e for e in events if e.get("decision") == "allow"]
    assert allows, f"no allow audit: {events}"
    assert allows[0]["tool_name"] == "fetch"


@pytest.mark.asyncio
async def test_ext_proxy_audits_sse_result_block():
    req = _ext_request_with_org({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                 "params": {"name": "fetch", "arguments": _BENIGN_ARG}})
    sse = _sse_resp({"jsonrpc": "2.0", "id": 9, "result": {"content": _PII_RESULT}})
    events = []

    async def _cap(**kw):
        events.append(kw)

    with patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", new_callable=AsyncMock, return_value=None), \
         patch.object(mcp_proxy, "_mcp_security_scan", side_effect=_output_scan_raises()), \
         patch.object(mcp_proxy, "_record_gateway_event", new=_cap), \
         patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(sse)):
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    assert any(e.get("decision") == "block" for e in events), f"SSE block not audited: {events}"
