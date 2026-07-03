"""CHG-0098: scan the ext-proxy NON-FINITE SSE stream (server notifications) per event.

Finite methods (tools/call, resources/*, prompts/*, initialize) buffer+scan their SSE
RESULT, but a NON-finite stream (server-pushed notifications / subscriptions / long-lived
streams) used to be forwarded RAW — buffering the whole open stream could hang/OOM. An
untrusted upstream can push sensitive data in a ``notifications/message`` frame, so the
raw passthrough was a real egress leak. The ext proxy now scans PER EVENT: it buffers
only up to one SSE event (bounded by ``_MCP_SSE_EVENT_MAX_BYTES`` — memory-safe),
reassembles + scans each JSON-RPC message (result/error AND notification params) via the
result floor, and re-emits; an over-cap unterminated event is withheld (fail-closed).

These tests drive the REAL ``ext_mcp_proxy`` streaming path and assert on the streamed
bytes.
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


def _sse_aiter(*frames: str):
    async def _gen():
        for f in frames:
            yield f.encode("utf-8")
    return _gen


def _upstream_sse(*frames: str):
    r = AsyncMock()
    r.status_code = 200
    r.headers = {"content-type": "text/event-stream"}
    r.aiter_bytes = _sse_aiter(*frames)
    r.aclose = AsyncMock()
    return r


def _ext_client(resp):
    c = AsyncMock()
    c.build_request = lambda **k: SimpleNamespace()
    c.send = AsyncMock(return_value=resp)
    c.aclose = AsyncMock()
    return c


async def _drive_stream(frames, *, method="resources/subscribe", extra_patches=()):
    req = SimpleNamespace(
        method="POST", headers={"content-type": "application/json"}, query_params={},
        body=AsyncMock(return_value=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": {"uri": "x"}}).encode()))
    audit = AsyncMock()
    mgrs = [
        patch.object(mcp_proxy, "_record_gateway_event", audit),
        patch.object(mcp_proxy, "is_safe_outbound_url", return_value=(True, "")),
        patch.object(mcp_proxy, "_mcp_org_rate_limit_raw", AsyncMock(return_value=None)),
        patch.object(mcp_proxy.httpx, "AsyncClient", return_value=_ext_client(_upstream_sse(*frames))),
        *extra_patches,
    ]
    with contextlib.ExitStack() as stack:
        for m in mgrs:
            stack.enter_context(m)
        resp = await mcp_proxy.ext_mcp_proxy(f"{_EXT_HOST}/mcp", req)
        out = b""
        async for chunk in resp.body_iterator:
            out += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
    reasons = [c.kwargs.get("reason") for c in audit.call_args_list]
    return out.decode("utf-8", "replace"), reasons


@pytest.mark.asyncio
async def test_notification_with_secret_is_masked_in_stream():
    frame = ('data: {"jsonrpc":"2.0","method":"notifications/message","params":'
             '{"data":"key AKIAIOSFODNN7EXAMPLE email bob@corp.example ip 10.9.8.7"}}\n\n')
    out, reasons = await _drive_stream([frame])
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "bob@corp.example" not in out
    assert "10.9.8.7" not in out
    assert "sse_stream_scanned" in reasons  # the stream is now audited as scanned


@pytest.mark.asyncio
async def test_benign_notifications_pass_through():
    frames = [
        'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":42}}\n\n',
        'data: {"jsonrpc":"2.0","method":"notifications/message","params":{"data":"all good"}}\n\n',
    ]
    out, _ = await _drive_stream(frames)
    assert "progress" in out and "42" in out
    assert "all good" in out


@pytest.mark.asyncio
async def test_multiline_split_notification_reassembled_and_masked():
    """A notification split across data: lines at a structural point is reassembled
    (CHG-0093) then scanned — the secret cannot slip through the stream."""
    frame = ('data: {"jsonrpc":"2.0","method":"notifications/message","params":\n'
             'data: {"data":"secret AKIAIOSFODNN7EXAMPLE here"}}\n\n')
    out, _ = await _drive_stream([frame])
    assert "AKIAIOSFODNN7EXAMPLE" not in out


@pytest.mark.asyncio
async def test_unmaskable_survivor_notification_withheld():
    """A notification mixing a maskable IP with an unmaskable private file path fails
    CLOSED — that event is withheld, not streamed raw."""
    frame = ('data: {"jsonrpc":"2.0","method":"notifications/message","params":'
             '{"data":"box 10.9.8.7 served /home/bob/.ssh/id_rsa"}}\n\n')
    out, reasons = await _drive_stream([frame])
    assert "id_rsa" not in out
    assert "10.9.8.7" not in out
    assert "withheld" in out
    assert "sse_stream_event_withheld" in reasons


@pytest.mark.asyncio
async def test_oversized_unterminated_event_withheld(monkeypatch):
    """An event that exceeds the per-event cap without a boundary is withheld
    (fail-closed) — an untrusted upstream cannot force unbounded buffering."""
    monkeypatch.setattr(mcp_proxy, "_MCP_SSE_EVENT_MAX_BYTES", 100)
    huge = "data: " + ("A" * 500)  # no trailing blank line -> unterminated
    out, reasons = await _drive_stream([huge])
    assert "oversized" in out
    assert "AAAAAAAA" not in out  # the oversized buffer was dropped, not streamed
    assert "sse_stream_event_too_large" in reasons


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
