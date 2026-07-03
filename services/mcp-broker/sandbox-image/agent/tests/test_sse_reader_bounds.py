"""CHG-0128: the in-sandbox SSE reader (_sse_reader_loop) reads an UNTRUSTED upstream
MCP server's event stream. It accumulated all `data:` lines of an event into `data_lines`
with NO size cap, so a malicious/broken upstream streaming unbounded data: lines before a
terminating blank line could OOM the sandbox agent (crashing ALL of that org's servers in
the shared sandbox). The reader now caps per-event data accumulation at _MAX_RESPONSE_BYTES
and DROPS the oversized event while continuing to serve subsequent events.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
if str(SANDBOX_IMAGE) not in sys.path:
    sys.path.insert(0, str(SANDBOX_IMAGE))

from agent import sse_manager  # noqa: E402
from agent.upstream_manager import UpstreamSession  # noqa: E402


class _FakeSSEResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}

    async def __aenter__(self) -> "_FakeSSEResponse":
        return self

    async def __aexit__(self, *_a) -> bool:
        return False

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def stream(self, _method, _url, **_kw):
        return _FakeSSEResponse(self._lines)


def _session(lines: list[str]) -> UpstreamSession:
    return UpstreamSession(
        server_slug="srv",
        transport="sse",
        url="http://up.example/sse",
        allowed_hosts=[],
        headers={},
        client=_FakeClient(lines),
    )


def _drain(sess: UpstreamSession) -> list[dict]:
    out = []
    q = sess.sse_responses
    while q is not None and not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_normal_message_event_delivered():
    lines = [
        "event: message",
        'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
        "",
    ]
    sess = _session(lines)
    await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert _drain(sess) == [{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}]


@pytest.mark.asyncio
async def test_oversized_event_dropped_and_reader_survives():
    big = "x" * 50
    lines = [
        "event: message",
        f"data: {big}",   # 50 bytes
        f"data: {big}",   # 100 > cap(80) → skip the rest of THIS event
        f"data: {big}",   # skipped
        "",               # oversized event ends → dropped, NOT delivered
        "event: message",
        'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}',
        "",               # next normal event → still delivered (reader survived)
    ]
    sess = _session(lines)
    with patch.object(sse_manager, "_MAX_RESPONSE_BYTES", 80):
        await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    # ONLY the small event after the oversized one is delivered.
    assert _drain(sess) == [{"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}]


@pytest.mark.asyncio
async def test_multiline_data_under_cap_still_joined():
    # A legitimate multi-line data event under the cap is joined with "\n" and parsed.
    lines = [
        "event: message",
        'data: {"jsonrpc":"2.0","id":3,',
        'data: "result":{"v":1}}',
        "",
    ]
    sess = _session(lines)
    await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert _drain(sess) == [{"jsonrpc": "2.0", "id": 3, "result": {"v": 1}}]


# ── CHG-0129: bound the sse_responses queue (unsolicited-flood OOM guard) ─────

@pytest.mark.asyncio
async def test_bounded_put_drops_oldest_when_full():
    q = asyncio.Queue(maxsize=2)
    sse_manager._bounded_put(q, {"id": 1}, "srv")
    sse_manager._bounded_put(q, {"id": 2}, "srv")
    sse_manager._bounded_put(q, {"id": 3}, "srv")   # full → evict oldest (id 1)
    assert q.qsize() == 2
    assert [q.get_nowait(), q.get_nowait()] == [{"id": 2}, {"id": 3}]


@pytest.mark.asyncio
async def test_unsolicited_message_flood_keeps_queue_bounded():
    # No consumer drains; an untrusted upstream floods 50 unsolicited message events.
    # The (patched-small) bounded queue must stay <= maxsize and RETAIN the freshest.
    lines: list[str] = []
    for i in range(50):
        lines += ["event: message", f'data: {{"jsonrpc":"2.0","id":{i},"result":{{}}}}', ""]
    sess = _session(lines)
    with patch.object(sse_manager, "_SSE_QUEUE_MAXSIZE", 8):
        await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert sess.sse_responses is not None
    assert sess.sse_responses.qsize() <= 8          # never grew unbounded
    ids = [d["id"] for d in _drain(sess)]
    assert 49 in ids and 0 not in ids               # drop-oldest kept the freshest
