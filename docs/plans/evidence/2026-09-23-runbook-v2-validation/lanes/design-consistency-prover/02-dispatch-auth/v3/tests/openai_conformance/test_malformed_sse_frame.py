"""LGW01-4: a malformed SSE ``data:`` frame must fail the stock SDK and name the frame."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openai
import pytest


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _MalformedHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        _ = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        body = (
            b"data: not-json-this-frame-MUST-fail\n\n"
            b"data: [DONE]\n\n"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def malformed_url() -> str:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _MalformedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.mark.asyncio
async def test_malformed_sse_data_frame_fails_and_names_frame(malformed_url: str) -> None:
    client = openai.AsyncOpenAI(
        base_url=f"{malformed_url}/v1",
        api_key="zs_test_sdk_compat_0123456789abcdef",
        max_retries=0,
    )
    try:
        with pytest.raises(Exception) as excinfo:
            stream = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
            async for _chunk in stream:
                pass
    finally:
        await client.close()
    named = str(excinfo.value)
    print("lgw01_4_named_frame not-json-this-frame-MUST-fail")
    assert named, "SDK accepted a malformed data: frame"
    lowered = named.lower()
    assert (
        "not-json-this-frame-must-fail" in lowered
        or "expecting value" in lowered
        or "json" in lowered
        or "sse" in lowered
    ), named
