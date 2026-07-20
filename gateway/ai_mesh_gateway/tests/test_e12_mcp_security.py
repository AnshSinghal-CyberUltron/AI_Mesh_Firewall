"""E12 — three recon-confirmed MCP security fixes.

FIX 1  tool-arg credential hard-block: a credential/secret in tool ARGUMENTS is
       force-blocked even when the resolved scan_action defaults to "tag"
       (detect-but-allow). Benign args pass.
FIX 2  stdio/websocket result-redaction: the adapter transport path runs the
       SAME outbound output-scan + redaction the streamable-http path uses, so a
       secret/PII in a stdio/ws tool RESULT is redacted (not raw) on return.
FIX 3  least-privilege key controls: GatewayAPIKey.mcp_allowed_tools (allowlist,
       empty = all) + mcp_max_tool_calls (per-turn cap, 0 = unlimited) are now
       extracted into AuthContext and enforced at the tool-call site.

Run:
    cd .../gateway && .venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_e12_mcp_security.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Bootstrap import paths. `_GW` = ai_mesh_gateway/ (parents[1] of tests/) holds
# mcp_proxy/middleware/patterns and the gateway's own jobs.py. The shared
# `ai_mesh_shared` package lives at <repo>/shared (repo root = _GW.parents[1]);
# that dir is appended LAST so it can never shadow the gateway's local jobs.py.
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


# A credential that detect_secrets (SECRET_PATTERNS) reliably flags as a secret
# → orchestrator tags it threat_type="secret".
_CRED_ARG = {"config": "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"}
_BENIGN_ARG = {"q": "what is the weather in Paris today"}
_CRED_RESULT_TEXT = "here is the api token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 ok"


# ── shared fakes ────────────────────────────────────────────────────────────


def _make_request(auth):
    return SimpleNamespace(state=SimpleNamespace(auth_context=auth))


def _auth(org_slug="demo", allowed_tools=None, max_calls=0):
    payload = {
        "key_id": "k1",
        "user_id": 1,
        "project_id": "p1",
        "org_slug": org_slug,
        "mcp_allowed_tools": allowed_tools if allowed_tools is not None else [],
        "mcp_max_tool_calls": max_calls,
    }
    return AuthContext(key_hash="h" * 64, payload=payload)


async def _run_jsonrpc(request, body, *, server_config, enabled_info,
                       adapter_resp=None):
    """Drive org_mcp_jsonrpc with backend/adapter dependencies stubbed."""
    patches = [
        patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value=server_config)),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        # never block via tool-call cap unless a test opts in
        patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=1)),
    ]
    if adapter_resp is not None:
        patches.append(
            patch.object(mcp_proxy, "_adapter_forward",
                         AsyncMock(return_value=adapter_resp)))

    request.json = AsyncMock(return_value=body)

    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        if adapter_resp is not None:
            with patches[5]:
                return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)
        return await mcp_proxy.org_mcp_jsonrpc("demo", "srv", request)


def _decode(resp):
    import json
    return json.loads(resp.body.decode("utf-8"))


# ── FIX 1: tool-arg credential hard-block ────────────────────────────────────


@pytest.mark.asyncio
async def test_fix1_credential_in_args_blocked_under_tag_default():
    """A secret in tool ARGS is BLOCKED even though scan_action defaults to tag."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "echo", "arguments": _CRED_ARG}}
    # streamable-http (default transport) → no adapter, backend stubbed but never
    # reached because the credential blocks pre-forward.
    with patch.object(mcp_proxy, "_mcp_block_on_credential_enabled",
                      return_value=True):
        resp = await _run_jsonrpc(
            req, body,
            server_config={"transport": "streamable-http"},
            enabled_info=None,  # → default scan_action "tag"
        )
    data = _decode(resp)
    assert data["result"]["isError"] is True
    assert "[BLOCKED]" in data["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_fix1_benign_args_pass_through():
    """A benign arg (no secret) is not blocked by the credential gate."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: {"result": "sunny"}
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=backend_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake_client),
    ):
        resp = await _run_jsonrpc(
            req, body,
            server_config={"transport": "streamable-http"},
            enabled_info=None,
        )
    data = _decode(resp)
    # not an error block; benign content flowed through
    assert data["result"].get("isError") is not True
    assert "[BLOCKED]" not in str(data["result"])


def test_fix1_findings_have_credential_helper():
    """Helper flags credentials (threat_type/SECRET tag/secret entity) not PII."""
    # clean secret-only finding
    assert mcp_proxy._findings_have_credential([{"threat_type": "secret"}]) is True
    # SECRET compliance tag (credential types map to it; PII never does)
    assert mcp_proxy._findings_have_credential([], ["SECRET"]) is True
    # combined pii+secret finding the orchestrator labels "pii" — still a creds hit
    assert mcp_proxy._findings_have_credential(
        [{"threat_type": "pii", "entity_type": "github_token",
          "detail": "Matched: github_token, token_assignment"}]) is True
    # pure PII is NOT a credential
    assert mcp_proxy._findings_have_credential(
        [{"threat_type": "pii", "entity_type": "email",
          "detail": "Matched: email"}], ["PII", "GDPR"]) is False
    assert mcp_proxy._findings_have_credential([{"threat_type": "pii"}]) is False
    assert mcp_proxy._findings_have_credential([]) is False
    assert mcp_proxy._findings_have_credential(None) is False


# ── FIX 2: stdio/websocket result-redaction ──────────────────────────────────


@pytest.mark.asyncio
async def test_fix2_stdio_result_secret_redacted():
    """A secret in a stdio tool RESULT is redacted (not raw) on return."""
    req = _make_request(_auth())
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    # adapter returns a JSON-RPC result carrying a secret in cleartext.
    from fastapi.responses import JSONResponse
    raw = JSONResponse(content={
        "jsonrpc": "2.0", "id": 9,
        "result": {"content": [{"type": "text", "text": _CRED_RESULT_TEXT}]},
    }, status_code=200)
    resp = await _run_jsonrpc(
        req, body,
        server_config={"transport": "stdio", "command": "x"},
        enabled_info={
            # redact action so the orchestrator mutates the output
            "default_scan_action": "redact",
        },
        adapter_resp=raw,
    )
    out = _decode(resp)
    blob = str(out)
    # The raw credential must NOT survive — either redacted or hard-blocked.
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in blob


# ── FIX 3: least-privilege mcp_allowed_tools + mcp_max_tool_calls ─────────────


def test_fix3_authcontext_extracts_mcp_fields():
    """AuthContext extracts mcp_allowed_tools + mcp_max_tool_calls from payload."""
    ac = AuthContext(key_hash="h" * 64, payload={
        "key_id": "k", "user_id": 1, "project_id": "p",
        "mcp_allowed_tools": ["echo", "search"],
        "mcp_max_tool_calls": 3,
    })
    assert ac.mcp_allowed_tools == ["echo", "search"]
    assert ac.mcp_max_tool_calls == 3
    # defaults
    ac2 = AuthContext(key_hash="h" * 64, payload={
        "key_id": "k", "user_id": 1, "project_id": "p"})
    assert ac2.mcp_allowed_tools == []
    assert ac2.mcp_max_tool_calls == 0


def test_fix3_tool_allowed_by_key():
    """Empty allowlist = all tools; non-empty blocks tools not in it."""
    empty = _auth(allowed_tools=[])
    assert mcp_proxy._tool_allowed_by_key("anything", empty) is True
    restricted = _auth(allowed_tools=["echo"])
    assert mcp_proxy._tool_allowed_by_key("echo", restricted) is True
    assert mcp_proxy._tool_allowed_by_key("delete_db", restricted) is False
    # no auth → permissive (not the place this is gated)
    assert mcp_proxy._tool_allowed_by_key("x", None) is True


def test_fix3_tool_call_cap_exceeded():
    """cap 0 = unlimited; >0 blocks once count exceeds cap."""
    assert mcp_proxy._tool_call_cap_exceeded(99, 0) is False   # unlimited
    assert mcp_proxy._tool_call_cap_exceeded(99, -1) is False  # unlimited
    assert mcp_proxy._tool_call_cap_exceeded(1, 2) is False    # under cap
    assert mcp_proxy._tool_call_cap_exceeded(2, 2) is False    # at cap
    assert mcp_proxy._tool_call_cap_exceeded(3, 2) is True     # over cap


@pytest.mark.asyncio
async def test_fix3_disallowed_tool_blocked_allowed_tool_passes():
    """A tool not in a non-empty allowlist is blocked; one in it is allowed."""
    # disallowed
    req = _make_request(_auth(allowed_tools=["echo"]))
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "delete_db", "arguments": {}}}
    resp = await _run_jsonrpc(
        req, body,
        server_config={"transport": "streamable-http"},
        enabled_info=None,
    )
    data = _decode(resp)
    assert data["result"]["isError"] is True
    assert "tool not allowed for this key" in data["result"]["content"][0]["text"]

    # allowed tool → reaches backend (stubbed), not the allowlist block
    req2 = _make_request(_auth(allowed_tools=["echo"]))
    body2 = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: {"result": "ok"}
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=backend_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake_client):
        resp2 = await _run_jsonrpc(
            req2, body2,
            server_config={"transport": "streamable-http"},
            enabled_info=None,
        )
    data2 = _decode(resp2)
    assert "tool not allowed for this key" not in str(data2)


@pytest.mark.asyncio
async def test_fix3_empty_allowlist_allows_all():
    """EMPTY allowlist must NOT block (all tools allowed)."""
    req = _make_request(_auth(allowed_tools=[]))
    body = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "any_tool", "arguments": _BENIGN_ARG}}
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: {"result": "ok"}
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=backend_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    with patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake_client):
        resp = await _run_jsonrpc(
            req, body,
            server_config={"transport": "streamable-http"},
            enabled_info=None,
        )
    assert "tool not allowed for this key" not in str(_decode(resp))


@pytest.mark.asyncio
async def test_fix3_max_tool_calls_cap_enforced_and_unlimited():
    """cap>0 blocks once exceeded; cap 0 never blocks on count."""
    # cap=1, this is the 2nd call in the window → blocked
    req = _make_request(_auth(max_calls=1))
    body = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    with patch.object(mcp_proxy, "_incr_tool_call_count", AsyncMock(return_value=2)):
        # _run_jsonrpc's own _incr patch is overridden by this inner one
        with (
            patch.object(mcp_proxy, "_validate_org_scope", return_value=None),
            patch.object(mcp_proxy, "_get_server_config",
                         AsyncMock(return_value={"transport": "streamable-http"})),
            patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
            patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        ):
            req.json = AsyncMock(return_value=body)
            resp = await mcp_proxy.org_mcp_jsonrpc("demo", "srv", req)
    data = _decode(resp)
    assert data["result"]["isError"] is True
    assert "tool-call limit exceeded" in data["result"]["content"][0]["text"]

    # cap=0 (unlimited): even a high count never blocks
    req2 = _make_request(_auth(max_calls=0))
    body2 = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "echo", "arguments": _BENIGN_ARG}}
    backend_resp = AsyncMock()
    backend_resp.status_code = 200
    backend_resp.json = lambda: {"result": "ok"}
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=backend_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    incr = AsyncMock(return_value=999)
    with (
        patch.object(mcp_proxy, "_incr_tool_call_count", incr),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=fake_client),
    ):
        resp2 = await _run_jsonrpc(
            req2, body2,
            server_config={"transport": "streamable-http"},
            enabled_info=None,
        )
    # unlimited → counter never even consulted (cap<=0 short-circuits) and no block
    assert "tool-call limit exceeded" not in str(_decode(resp2))
    incr.assert_not_called()
