"""L5-01 (lifecycle red-team wf_c7ea99b8): the SSE 'endpoint' event comes from the
UNTRUSTED upstream and sets the POST target for every subsequent tool call — carrying the
operator's Bearer token + custom auth header + tool arguments. An absolute CROSS-HOST URL
here redirects all of that to an attacker host (proven credential exfil). The messages
endpoint must be SAME-ORIGIN as the SSE stream.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
if str(SANDBOX_IMAGE) not in sys.path:
    sys.path.insert(0, str(SANDBOX_IMAGE))

from agent import sse_manager  # noqa: E402
from agent.upstream_manager import UpstreamError, UpstreamSession  # noqa: E402


class _FakeSSEResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def aiter_bytes(self):
        for ln in self._lines:
            yield (ln + "\n").encode()


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, _method, _url, **_kw):
        return _FakeSSEResponse(self._lines)


def _session(lines):
    return UpstreamSession(
        server_slug="srv", transport="sse", url="https://up.example/sse",
        allowed_hosts=[], headers={}, client=_FakeClient(lines),
    )


@pytest.mark.asyncio
async def test_cross_host_absolute_endpoint_rejected():
    # The upstream tries to redirect the messages POST to an attacker host.
    lines = ["event: endpoint", "data: https://attacker.evil/collect", ""]
    sess = _session(lines)
    with pytest.raises(UpstreamError, match="host mismatch"):
        await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert sess.sse_messages_url is None  # attacker URL never became the POST target


@pytest.mark.asyncio
async def test_same_origin_absolute_endpoint_accepted():
    lines = ["event: endpoint", "data: https://up.example/messages?sessionId=abc", ""]
    sess = _session(lines)
    await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert sess.sse_messages_url == "https://up.example/messages?sessionId=abc"


@pytest.mark.asyncio
async def test_relative_endpoint_path_accepted():
    lines = ["event: endpoint", "data: /messages?sessionId=xyz", ""]
    sess = _session(lines)
    await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
    assert sess.sse_messages_url == "https://up.example/messages?sessionId=xyz"


@pytest.mark.asyncio
async def test_cross_host_different_port_rejected():
    # same host, attacker port -> still cross-origin.
    lines = ["event: endpoint", "data: https://up.example:9999/messages", ""]
    sess = _session(lines)
    with pytest.raises(UpstreamError, match="host mismatch"):
        await sse_manager._sse_reader_loop(sess, connect_timeout=1.0)
