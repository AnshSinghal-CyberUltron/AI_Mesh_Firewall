"""Phase-A boundary validation (2026-07-31) — Bundles B + C.

Rejects malformed / oversized / bad-encoding MCP tool-call input AT the gateway with clean,
correctly-LABELED 4xx (instead of swallowing, forwarding raw, mislabeling as credential/PII, or
timing out upstream), and adds the missing input node-count cap + chat tool-metadata size limit +
orphan-tool-message rejection.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
from middleware import AuthContext  # noqa: E402


def _auth(org_slug="demo"):
    return AuthContext(key_hash="h" * 64, payload={"user_id": 1, "org_slug": org_slug})


def _rest_request(auth, body_bytes, content_type="application/json"):
    req = SimpleNamespace(state=SimpleNamespace(auth_context=auth))
    req.body = AsyncMock(return_value=body_bytes)
    req.headers = {"content-type": content_type}
    return req


def _decode(resp):
    return json.loads(bytes(resp.body))


def _http_resp(json_body, *, status=200):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": "application/json"}
    return r


def _fake_client(resp):
    c = AsyncMock()
    c.post = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


# ── A-10: malformed JSON -> clean gateway 400 invalid_json ────────────────────
@pytest.mark.asyncio
async def test_a10_truncated_json_returns_gateway_invalid_json_400():
    req = _rest_request(_auth(), b'{"name":"get_me","arguments":{')
    with patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 400
    body = _decode(resp)
    assert body["code"] == "invalid_json"
    assert body.get("request_id")
    assert resp.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_a10_empty_body_stays_lenient_not_invalid_json():
    # empty body must NOT be treated as malformed (it falls through to the missing-name path)
    req = _rest_request(_auth(), b"")
    backend = _http_resp({"error": "Missing 'name' field"}, status=400)
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client(backend)),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert _decode(resp).get("code") != "invalid_json"


# ── A-09: lone surrogate -> clean gateway 400 invalid_encoding ────────────────
@pytest.mark.asyncio
async def test_a09_lone_surrogate_rejected_invalid_encoding():
    req = _rest_request(_auth(), b'{"name":"search","arguments":{"query":"\\ud800 orphan"}}')
    with patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 400
    assert _decode(resp)["code"] == "invalid_encoding"


@pytest.mark.asyncio
async def test_a09_valid_surrogate_pair_emoji_allowed():
    # a VALID surrogate pair (emoji) must NOT be rejected
    req = _rest_request(_auth(), b'{"name":"search","arguments":{"query":"\\ud83d\\ude00 hi"}}')
    backend = _http_resp({"result": [{"type": "text", "text": "ok"}]})
    with (
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_fake_client(backend)),
    ):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert _decode(resp).get("code") != "invalid_encoding"


# ── A-02: oversized args -> clean gateway 413 args_too_large ──────────────────
@pytest.mark.asyncio
async def test_a02_oversized_args_returns_413_args_too_large():
    big = "A" * (mcp_proxy._MCP_MAX_ARGS_BYTES + 1000)
    req = _rest_request(_auth(), json.dumps({"name": "search", "arguments": {"query": big}}).encode())
    with patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    assert resp.status_code == 413
    body = _decode(resp)
    assert body["code"] == "args_too_large"
    assert "credential" not in json.dumps(body).lower()  # NOT mislabeled


# ── A-02 / A-03: honest inbound block reasons ─────────────────────────────────
def test_inbound_block_reason_scan_error_not_credential():
    reason, detail = mcp_proxy._inbound_block_reason("t", [], {"arg_scan_error": True})
    assert reason == "arg_scan_error"
    assert "credential" not in detail.lower()


def test_inbound_block_reason_depth_not_pii():
    reason, _ = mcp_proxy._inbound_block_reason("t", ["RESOURCE_LIMIT"], {"args_too_deeply_nested": True, "max_arg_depth": 200})
    assert reason == "args_too_deeply_nested"


def test_inbound_block_reason_nodes():
    reason, _ = mcp_proxy._inbound_block_reason("t", ["RESOURCE_LIMIT"], {"args_too_many_nodes": True, "max_arg_nodes": 50000})
    assert reason == "args_too_many_nodes"


def test_inbound_block_reason_real_pii_keeps_wording():
    reason, detail = mcp_proxy._inbound_block_reason("t", ["PII"], {})
    assert reason == "pii_blocked_inbound" and "PII" in detail


# ── A-04: input node-count cap (via _scan_tool_args_block) ────────────────────
@pytest.mark.asyncio
async def test_a04_wide_array_blocked_by_node_cap_under_enforcing_posture():
    args = {"arr": ["y"] * (mcp_proxy._MCP_MAX_ARG_NODES + 10)}
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_args_block(
        args, tool_name="search", enabled_info={"default_scan_action": "block"},
        org_slug="o", server_slug="s")
    assert blocked is True
    assert tags == ["RESOURCE_LIMIT"]
    assert meta.get("args_too_many_nodes") is True
    # honest reason for the response
    reason, _ = mcp_proxy._inbound_block_reason("search", tags, meta)
    assert reason == "args_too_many_nodes"


@pytest.mark.asyncio
async def test_a04_wide_array_forwarded_under_monitor_posture():
    args = {"arr": ["y"] * (mcp_proxy._MCP_MAX_ARG_NODES + 10)}
    with patch.object(mcp_proxy, "_explicit_monitor_posture", return_value=True):
        scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_args_block(
            args, tool_name="search", enabled_info={"default_scan_action": "monitor"},
            org_slug="o", server_slug="s")
    assert blocked is False  # observe-only never blocks


@pytest.mark.asyncio
async def test_a04_normal_small_args_not_capped():
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_args_block(
        {"query": "zeroshield firewall"}, tool_name="search",
        enabled_info={"default_scan_action": "block"}, org_slug="o", server_slug="s")
    assert meta.get("args_too_many_nodes") is not True


# ── chat-path (A-19 / A-21) — real gateway app via the SDK-compat harness ─────
import httpx  # noqa: E402
import ai_mesh_gateway.tests.test_openai_sdk_compat as T  # noqa: E402


async def _post_chat(app, body):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {T.API_KEY}", "Content-Type": "application/json"},
            content=json.dumps(body),
        )


# ── A-19: 100KB tool description -> 400 tool_metadata_too_large (not a DoS block) ──
@pytest.mark.asyncio
async def test_a19_oversized_tool_metadata_returns_size_error(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    try:
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{
                "type": "function",
                "function": {"name": "f", "description": "D" * 100_000, "parameters": {}},
            }],
        }
        resp = await _post_chat(app, body)
        assert resp.status_code == 400
        j = resp.json()
        # OpenAI-compat shim nests the code under error.{} (same envelope as content_filter)
        assert j["error"]["code"] == "tool_metadata_too_large"
        assert "dos" not in json.dumps(j).lower() and "content_filter" not in json.dumps(j).lower()
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_a19_normal_tool_metadata_passes(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    try:
        body = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "Get the weather for a city.",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
        }
        resp = await _post_chat(app, body)
        assert resp.status_code == 200  # reaches mocked upstream
    finally:
        await auth_redis.aclose()


# ── A-21: orphan role=tool message -> 400 (OpenAI parity); valid flow -> 200 ───
@pytest.mark.asyncio
async def test_a21_orphan_tool_message_rejected(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    try:
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "tool", "tool_call_id": "call_never_issued", "content": "orphan result"},
            ],
        }
        resp = await _post_chat(app, body)
        assert resp.status_code == 400
        j = resp.json()
        assert j["error"]["code"] == "invalid_messages"
        assert "orphan" in j["error"].get("message", "").lower()
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_a21_valid_tool_conversation_passes(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    try:
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "weather in NYC?"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_abc", "type": "function",
                     "function": {"name": "get_weather", "arguments": "{\"city\":\"NYC\"}"}}]},
                {"role": "tool", "tool_call_id": "call_abc", "content": "sunny, 72F"},
            ],
        }
        resp = await _post_chat(app, body)
        assert resp.status_code == 200  # valid sequence must NOT be flagged as orphan
    finally:
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_a21_legacy_function_role_unaffected(monkeypatch):
    # legacy function-calling format (role=function, no tool_call_id) must NOT be rejected
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    try:
        body = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "weather?"},
                {"role": "function", "name": "get_weather", "content": "sunny"},
            ],
        }
        resp = await _post_chat(app, body)
        assert resp.status_code == 200
    finally:
        await auth_redis.aclose()
