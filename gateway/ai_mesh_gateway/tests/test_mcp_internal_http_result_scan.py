"""CHG-0106: the internal (chat-pipeline → MCP tool) route's LEGACY direct-httpx path
(``MCP_HTTP_VIA_SANDBOX=0``) scanned only ``result.content`` in ``_scan_internal_result``.
So a secret / PII / internal-IP in an upstream JSON-RPC ERROR FRAME (``error.message``) or
a ``structuredContent``-only result egressed RAW to the chat pipeline / LLM — a WEAKER leak
posture than the default sandbox path (CHG-0105) and the org path (CHG-0091). Now this path
scans the WHOLE result (content + structuredContent + bare string/list) OR the bare error
envelope via the shared floor, masks/blocks + AUDITS the redact — full parity.

The default production internal path (all transports sandbox-routed) is covered by
test_mcp_internal_sandbox_result_scan.py; this suite covers the legacy direct-httpx fallback.
"""
from __future__ import annotations

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


def _http_resp(json_body, *, status=200, content_type="application/json"):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: json_body
    r.text = json.dumps(json_body)
    r.headers = {"content-type": content_type}
    r.raise_for_status = lambda: None
    return r


def _fake_client(responses):
    seq = list(responses)

    async def _post(*_a, **_k):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    c = AsyncMock()
    c.post = AsyncMock(side_effect=_post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _internal_req():
    return SimpleNamespace(
        headers={"X-Gateway-Internal-Key": "ok"},
        json=AsyncMock(return_value={
            "org_slug": "demo", "server_slug": "srv", "tool_name": "fetch",
            "arguments": {"q": "hi"}}))


async def _drive_http(upstream_call_json):
    """Drive internal_tools_call on the LEGACY direct-httpx path. ``_is_sandbox_routed``
    is forced False (hermetic — no global env mutation) so the httpx branch that calls
    ``_scan_internal_result`` is exercised. The upstream sees init + notify + tools/call;
    only the third reply (``upstream_call_json``) carries the result under test."""
    init = _http_resp({"jsonrpc": "2.0", "id": 1, "result": {}})
    notif = _http_resp({})
    call = _http_resp(upstream_call_json)
    client = _fake_client([init, notif, call])
    audit = AsyncMock()
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=False),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://safe.example.com/mcp"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=None)),
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.internal_tools_call(_internal_req())
    return resp.body.decode("utf-8"), [c.kwargs.get("decision") for c in audit.call_args_list]


def _result(text):
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


@pytest.mark.asyncio
async def test_http_error_envelope_secret_ip_pii_masked():
    """The core CHG-0106 gap: a bare ERROR FRAME was returned UNSCANNED (only
    ``result.content`` was scanned). Now the whole error envelope is scanned."""
    blob, _ = await _drive_http({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32000,
                  "message": "connect failed key AKIAIOSFODNN7EXAMPLE host 10.1.2.3 user bob.jones@corp.example"}})
    assert "AKIAIOSFODNN7EXAMPLE" not in blob   # AWS key masked
    assert "10.1.2.3" not in blob               # internal IP redacted
    assert "bob.jones@corp.example" not in blob  # email masked


@pytest.mark.asyncio
async def test_http_structured_content_only_masked():
    """A result with only ``structuredContent`` (no ``content`` array) was returned
    UNSCANNED. The whole ``result`` is now scanned, so nested values are masked."""
    blob, decisions = await _drive_http({
        "jsonrpc": "2.0", "id": 1,
        "result": {"structuredContent": {"db": {"secret": "AKIAIOSFODNN7EXAMPLE",
                                                "ssn": "123-45-6789"}}}})
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "123-45-6789" not in blob
    assert "redact" in decisions  # redaction is now audited (prior omission)


@pytest.mark.asyncio
async def test_http_content_still_masked_no_regression():
    """Control: the classic ``result.content`` masking still works after the rewrite."""
    blob, _ = await _drive_http(_result("contact bob.jones@corp.example for access"))
    assert "bob.jones@corp.example" not in blob
    assert "b***@c***.example" in blob  # masked form present


@pytest.mark.asyncio
async def test_http_redact_is_audited():
    """The old code silently swapped masked content with NO audit event. The rewrite
    records a ``redact`` decision — audit parity with the sandbox / org paths."""
    _, decisions = await _drive_http(_result("email bob.jones@corp.example"))
    assert "redact" in decisions


@pytest.mark.asyncio
async def test_http_benign_result_preserved():
    """No false positive: a benign result flows unchanged with no block/redact audit."""
    blob, decisions = await _drive_http(_result("the weather in Paris is sunny"))
    assert "the weather in Paris is sunny" in blob
    assert "block" not in decisions and "redact" not in decisions


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
