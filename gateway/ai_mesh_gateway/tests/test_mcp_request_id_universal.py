"""Universal request_id (2026-07-30): every MCP gateway response carries a stable request_id
in its body AND an x-request-id header — including the early-return guards (scope / disabled /
body-too-large / content-type) that previously returned a blank id. Permanent fix: the id is
minted once (inbound header or generated) and threaded through every terminal path + a universal
response-header middleware.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402


def _req(headers=None):
    r = SimpleNamespace(state=SimpleNamespace())
    r.headers = headers or {}
    return r


def _decode(resp):
    return json.loads(bytes(resp.body))


# ── the id helper ─────────────────────────────────────────────────────────────
def test_ensure_request_id_generates_when_no_header():
    r = _req()
    rid = mcp_proxy._mcp_ensure_request_id(r)
    assert rid and rid.startswith("zs-mcp-")
    assert r.state.gw_request_id == rid
    # stable across calls (stashed on state)
    assert mcp_proxy._mcp_ensure_request_id(r) == rid


def test_ensure_request_id_honors_inbound_header():
    r = _req({"x-request-id": "caller-trace-123"})
    assert mcp_proxy._mcp_ensure_request_id(r) == "caller-trace-123"


# ── early guards carry request_id in body + header ────────────────────────────
@pytest.mark.asyncio
async def test_body_too_large_response_carries_request_id_and_audits():
    audit = AsyncMock()
    with patch.object(mcp_proxy, "_record_gateway_event", audit):
        resp = await mcp_proxy._mcp_body_too_large_response(
            request_id="zs-mcp-abc", org_slug="zeroshield", server_slug="s")
    assert resp.status_code == 413
    assert _decode(resp)["request_id"] == "zs-mcp-abc"
    assert resp.headers.get("x-request-id") == "zs-mcp-abc"
    audit.assert_awaited_once()
    assert audit.call_args.kwargs["reason"] == "body_too_large"
    assert audit.call_args.kwargs["request_id"] == "zs-mcp-abc"


@pytest.mark.asyncio
async def test_unsupported_media_type_response_carries_request_id_and_audits():
    audit = AsyncMock()
    with patch.object(mcp_proxy, "_record_gateway_event", audit):
        resp = await mcp_proxy._mcp_unsupported_media_type_response(
            request_id="zs-mcp-xyz", org_slug="zeroshield", server_slug="s")
    assert resp.status_code == 415
    assert _decode(resp)["request_id"] == "zs-mcp-xyz"
    assert resp.headers.get("x-request-id") == "zs-mcp-xyz"
    audit.assert_awaited_once()
    assert audit.call_args.kwargs["reason"] == "unsupported_media_type"


@pytest.mark.asyncio
async def test_server_disabled_response_carries_request_id():
    with (
        patch.object(mcp_proxy, "_get_server_config", AsyncMock(return_value={"is_active": False})),
        patch.object(mcp_proxy, "_server_disabled", return_value=True),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()) as audit,
    ):
        resp = await mcp_proxy._rest_server_disabled_response(
            _req(), "zeroshield", "s", request_id="zs-mcp-dis")
    assert resp.status_code == 403
    assert _decode(resp)["request_id"] == "zs-mcp-dis"
    assert resp.headers.get("x-request-id") == "zs-mcp-dis"
    assert audit.call_args.kwargs["request_id"] == "zs-mcp-dis"


def test_with_request_id_stamps_a_prebuilt_error_body():
    orig = JSONResponse(content={"error": "org_scope_violation"}, status_code=403)
    stamped = mcp_proxy._with_request_id(orig, "zs-mcp-scope")
    assert _decode(stamped)["request_id"] == "zs-mcp-scope"
    assert stamped.headers.get("x-request-id") == "zs-mcp-scope"
    assert stamped.status_code == 403


def test_with_request_id_is_noop_without_id_or_on_non_object():
    r = JSONResponse(content=["not", "an", "object"], status_code=400)
    assert mcp_proxy._with_request_id(r, "zs-mcp-x") is r      # non-object body untouched
    r2 = JSONResponse(content={"error": "x"}, status_code=400)
    assert mcp_proxy._with_request_id(r2, "") is r2            # no id -> noop
