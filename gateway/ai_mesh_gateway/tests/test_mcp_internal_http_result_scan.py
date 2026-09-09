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


# __PDD_PRESET_FIXTURE__

# policy-driven-detection cutover (task 9): the MCP built-in default detectors (the Tier-1
# PRESET pass) are now EFFECTIVE-DEFAULT OFF (mcp_scan_orchestrator._mcp_default_detection_enabled)
# so a zero-enabled-policy org is passthrough. This module exercises the RETAINED preset
# DETECTION MACHINERY (redaction / fail-closed byte-truth / exfil-defang / authz / audit), which
# stays reachable via the explicit opt-in env. Enable it for this module so those invariants are
# still tested. The default-OFF (Zero_Policy_State passthrough) contract is asserted by the
# dedicated test_policy_driven_* modules, not weakened here.
import os as _os_pdd


@pytest.fixture(autouse=True)
def _enable_builtin_mcp_presets(monkeypatch):
    monkeypatch.setenv("GATEWAY_MCP_DEFAULT_DETECTION", "true")
    monkeypatch.setenv("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true")
    yield


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


async def _drive_http(upstream_call_json, scan_action="redact"):
    """Drive internal_tools_call on the LEGACY direct-httpx path. ``_is_sandbox_routed``
    is forced False (hermetic — no global env mutation) so the httpx branch that calls
    ``_scan_internal_result`` is exercised. The upstream sees init + notify + tools/call;
    only the third reply (``upstream_call_json``) carries the result under test.

    ``scan_action`` is the posture the OPERATOR selected for this org. Enforcement is
    strictly operator-selected: with nothing selected (or an observe-only posture such as
    ``tag``/``monitor``) detection still runs but the payload is never mutated, so every
    masking/blocking assertion below explicitly selects an enforcing posture."""
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
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"default_scan_action": scan_action})),
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


# ── CHG-0123: the legacy internal streamable-http SSE branch parsed PER LINE and returned
# the FIRST parseable data: line — which could be a server-pushed NOTIFICATION (wrong
# response) — and dropped a multi-line-data result. It now parses PER EVENT, reassembles
# data: fields, and returns the RESULT/ERROR event (parity with CHG-0093/0122).


def _sse_resp(sse_text):
    r = AsyncMock()
    r.status_code = 200
    r.json = lambda: {}
    r.text = sse_text
    r.headers = {"content-type": "text/event-stream"}
    r.raise_for_status = lambda: None
    return r


async def _drive_http_sse(sse_text, *, enabled_info=None):
    init = _http_resp({"jsonrpc": "2.0", "id": 1, "result": {}})
    notif = _http_resp({})
    call = _sse_resp(sse_text)
    client = _fake_client([init, notif, call])
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=False),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://safe.example.com/mcp"})),
        patch.object(mcp_proxy, "_get_enabled_tools", AsyncMock(return_value=enabled_info)),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.internal_tools_call(_internal_req())
    return resp.body.decode("utf-8")


@pytest.mark.asyncio
async def test_sse_returns_result_not_leading_notification():
    sse = ('data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":50}}\n\n'
           'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"THE_ANSWER"}]}}\n\n')
    body = await _drive_http_sse(sse)
    assert "THE_ANSWER" in body        # the actual result event is returned
    assert "progress" not in body      # the leading notification is NOT returned


@pytest.mark.asyncio
async def test_sse_reassembles_multiline_data_result():
    sse = ('data: {"jsonrpc":"2.0","id":1,"result":\n'
           'data: {"content":[{"type":"text","text":"MULTILINE_OK"}]}}\n\n')
    body = await _drive_http_sse(sse)
    assert "MULTILINE_OK" in body      # multi-line data reassembled, not dropped as Empty SSE


@pytest.mark.asyncio
async def test_sse_result_secret_still_masked():
    sse = 'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"key AKIAIOSFODNN7EXAMPLE"}]}}\n\n'
    body = await _drive_http_sse(sse, enabled_info={"default_scan_action": "redact"})
    assert "AKIAIOSFODNN7EXAMPLE" not in body   # the returned result is still floor-scanned


# ── CHG-0124: end-to-end regression-lock (no production code change) for the CHAT-PATH
# tool authorization invariant. internal_tools_call blocks an org-DISABLED tool near the
# top of the handler (`_is_tool_disabled` -> -32000) BEFORE any upstream forward. There was
# NO end-to-end test proving the block SHORT-CIRCUITS execution — only unit coverage of
# `_is_tool_disabled`/the enabled-tools cache. With many parallel sessions churning
# mcp_proxy.py, this locks in "a disabled tool never executes on EITHER forward path
# (sandbox `_adapter_forward` OR legacy httpx), so nothing egresses to the chat pipeline".


async def _drive_disabled(*, disabled=("fetch",), sandbox_routed=True):
    """Drive internal_tools_call with the (hardcoded `fetch`) tool. Spies BOTH forward
    mechanisms: `_adapter_forward` (sandbox/broker path) and the httpx client.post
    (legacy path). Returns (resp, httpx_post_spy, adapter_forward_spy)."""
    client = _fake_client([_http_resp(
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}})])
    fwd = AsyncMock(return_value=_http_resp(
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}))
    with (
        patch.object(mcp_proxy, "_valid_internal_key", return_value=True),
        patch.object(mcp_proxy, "_is_sandbox_routed", return_value=sandbox_routed),
        patch.object(mcp_proxy, "_get_enabled_tools",
                     AsyncMock(return_value={"disabled": set(disabled), "known": {"fetch"}})),
        patch.object(mcp_proxy, "_get_server_config",
                     AsyncMock(return_value={"transport": "streamable-http",
                                             "url": "https://safe.example.com/mcp"})),
        patch.object(mcp_proxy, "_adapter_forward", fwd),
        patch.object(mcp_proxy, "_record_gateway_event", AsyncMock()),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_block_on_credential_enabled", return_value=True),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=client),
    ):
        resp = await mcp_proxy.internal_tools_call(_internal_req())
    return resp, client.post, fwd


@pytest.mark.asyncio
async def test_internal_disabled_tool_blocked_and_not_forwarded():
    # default routing (sandbox) — the disabled block precedes the routing decision.
    resp, httpx_post, adapter_forward = await _drive_disabled(disabled=("fetch",))
    body = json.loads(resp.body.decode("utf-8"))
    # (1) returns the JSON-RPC disabled error (policy visible to the caller)
    assert body.get("error", {}).get("code") == -32000
    assert "is disabled for this server" in body["error"]["message"]
    # (2) SECURITY INVARIANT: the tool never executed on EITHER forward path -> no egress.
    assert adapter_forward.call_count == 0   # sandbox/broker forward never happened
    assert httpx_post.call_count == 0        # legacy httpx forward never happened


@pytest.mark.asyncio
async def test_internal_enabled_tool_is_forwarded_control():
    """Positive control (legacy path): with NO tool disabled, the SAME harness DOES forward
    upstream (httpx client.post fires) — proving the no-forward assertion above is not
    vacuous (the harness can actually observe a forward)."""
    resp, httpx_post, _fwd = await _drive_disabled(disabled=(), sandbox_routed=False)
    assert httpx_post.call_count >= 1   # init/notify/call reached the upstream client


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
