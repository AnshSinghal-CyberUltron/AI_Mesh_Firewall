"""CHG-0095: audit the ext-proxy infra-error WITHHOLD / redact decisions.

The external MCP passthrough (``ext_mcp_proxy``) fails CLOSED on several infra paths —
upstream response too large (CHG-0064), non-JSON body, JSON-RPC error content, and
non-200 body (CHG-0061) — but those WITHHOLDS (and their redact counterparts) recorded
NO gateway audit event, so a fail-closed content block was invisible in the MCPEvent
trail (breaks the ...->tag->AUDIT chain, unlike the already-audited SSRF/credential/PII
blocks). These tests drive the REAL ``ext_mcp_proxy`` and assert the audit fires.
"""
from __future__ import annotations

import contextlib
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

_EXT_HOST = next(iter(mcp_proxy._ALLOWED_MCP_DOMAINS))


def _ext_request(body_obj, *, headers=None):
    req = SimpleNamespace()
    req.method = "POST"
    req.headers = headers or {"content-type": "application/json"}
    req.query_params = {}
    req.body = AsyncMock(return_value=json.dumps(body_obj).encode())
    return req


def _aiter_bytes_of(payload: bytes):
    async def _gen():
        yield payload
    return _gen


def _ext_json_resp(json_body, *, content_type="application/json", status=200):
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.json = lambda: json_body
    payload = json.dumps(json_body).encode()
    r.aread = AsyncMock(return_value=payload)
    r.aiter_bytes = _aiter_bytes_of(payload)
    r.aclose = AsyncMock()
    return r


def _ext_text_resp(text: str, *, content_type="text/plain", status=200):
    r = AsyncMock()
    r.status_code = status
    r.headers = {"content-type": content_type}

    def _raise():
        raise ValueError("not json")
    r.json = _raise
    payload = text.encode()
    r.aread = AsyncMock(return_value=payload)
    r.aiter_bytes = _aiter_bytes_of(payload)
    r.aclose = AsyncMock()
    return r


def _ext_client(send_resp):
    client = AsyncMock()
    client.build_request = lambda **_k: SimpleNamespace()
    client.send = AsyncMock(return_value=send_resp)
    client.aclose = AsyncMock()
    return client


def _audit_calls(audit_mock):
    """List of (decision, reason) from the captured _record_gateway_event calls."""
    return [(c.kwargs.get("decision"), c.kwargs.get("reason")) for c in audit_mock.call_args_list]


async def _drive(req, upstream, *, extra_patches=()):
    audit = AsyncMock()
    mgrs = [
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", AsyncMock(return_value=None)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(upstream)),
        *extra_patches,
    ]
    with contextlib.ExitStack() as stack:
        for m in mgrs:
            stack.enter_context(m)
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
    return resp, audit


def _call_body():
    return {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"q": "hi"}}}


@pytest.mark.asyncio
async def test_response_too_large_json_path_audited():
    req = _ext_request(_call_body())
    upstream = _ext_json_resp({"jsonrpc": "2.0", "id": 5, "result": {"content": []}})
    resp, audit = await _drive(
        req, upstream,
        extra_patches=[patch.object(mcp_proxy, "_read_response_capped",
                                    AsyncMock(side_effect=mcp_proxy._MCPBodyTooLarge()))],
    )
    assert ("block", "response_too_large") in _audit_calls(audit)


@pytest.mark.asyncio
async def test_response_too_large_sse_path_audited():
    req = _ext_request(_call_body())
    upstream = _ext_json_resp({"jsonrpc": "2.0", "id": 5, "result": {"content": []}},
                              content_type="text/event-stream")
    resp, audit = await _drive(
        req, upstream,
        extra_patches=[patch.object(mcp_proxy, "_read_response_capped",
                                    AsyncMock(side_effect=mcp_proxy._MCPBodyTooLarge()))],
    )
    assert ("block", "response_too_large") in _audit_calls(audit)


@pytest.mark.asyncio
async def test_nonjson_text_body_redaction_audited():
    req = _ext_request(_call_body())
    upstream = _ext_text_resp("contact jane.doe@corp.example for access", content_type="text/plain")
    resp, audit = await _drive(req, upstream)
    # the email is masked on egress AND the redact is audited
    body = resp.body.decode() if hasattr(resp, "body") else ""
    assert "jane.doe@corp.example" not in body
    assert ("redact", "text_body_redacted") in _audit_calls(audit)


@pytest.mark.asyncio
async def test_nonok_body_redaction_audited():
    req = _ext_request(_call_body())
    # non-200 body with NO result/error but carrying PII -> CHG-0061 whole-body scan
    upstream = _ext_json_resp({"detail": "user jane.doe@corp.example not found"}, status=500)
    resp, audit = await _drive(req, upstream)
    assert ("redact", "nonok_body_redacted") in _audit_calls(audit)


@pytest.mark.asyncio
async def test_request_too_large_audited():
    req = _ext_request(_call_body())
    upstream = _ext_json_resp({"jsonrpc": "2.0", "id": 5, "result": {"content": []}})
    resp, audit = await _drive(
        req, upstream,
        extra_patches=[patch.object(mcp_proxy, "_mcp_body_too_large", return_value=True)],
    )
    assert ("block", "request_too_large") in _audit_calls(audit)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
