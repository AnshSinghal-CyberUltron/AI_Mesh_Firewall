"""Tests for the E2E token-emitting stub (task 1.1).

The stub is load-bearing: if it emits nothing, `output_guardrail` reports 0 ms,
`full_nine_stages` goes False, and the harness measures a six-stage pipeline
while reporting nine. These tests pin the properties the harness depends on.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

STUB = Path(__file__).parent / "token_stub.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                return json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 — polling a starting server
            last = exc
            time.sleep(0.1)
    raise AssertionError(f"stub did not become healthy on :{port}: {last}")


@pytest.fixture
def stub():
    """Start a stub on a free port; yield (port, proc). Tokens/rate fixed for determinism."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(STUB), "--port", str(port),
         "--tokens", "40", "--rate", "200", "--ttft-ms", "20"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _wait_health(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _post(port: int, *, stream: bool):
    return urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"model": "m", "stream": stream,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"Content-Type": "application/json"},
    )


def test_refuses_zero_tokens():
    """R7.7: a silently-empty upstream must be impossible, not merely discouraged."""
    p = subprocess.run([sys.executable, str(STUB), "--tokens", "0"],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode != 0, "stub started with --tokens 0; it must refuse"
    assert "must be > 0" in p.stderr
    assert "full_nine_stages" in p.stderr, "refusal must explain the consequence"


def test_non_streaming_emits_real_content(stub):
    with urllib.request.urlopen(_post(stub, stream=False), timeout=30) as r:
        d = json.loads(r.read())
    assert d["id"].startswith("chatcmpl-e2estub-"), "stub must be identifiable as a stub"
    assert d["usage"]["completion_tokens"] == 40
    assert d["choices"][0]["finish_reason"] == "stop"
    # Output scanning must have real text to work on, or the measurement is hollow.
    assert len(d["choices"][0]["message"]["content"]) > 100


def _drain_sse(port: int):
    t0 = time.perf_counter()
    ttft = None
    content = 0
    done = False
    stamps: list[float] = []
    with urllib.request.urlopen(_post(port, stream=True), timeout=60) as r:
        assert r.headers.get("Content-Type", "").startswith("text/event-stream")
        buf = b""
        for raw in r:
            buf += raw
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                if not frame.startswith(b"data: "):
                    continue
                payload = frame[6:].decode()
                if payload.strip() == "[DONE]":
                    done = True
                    continue
                delta = json.loads(payload)["choices"][0].get("delta", {})
                if "content" in delta:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    content += 1
                    stamps.append(time.perf_counter())
    return {
        "total_ms": (time.perf_counter() - t0) * 1000,
        "ttft_ms": ttft, "content": content, "done": done,
        "gaps": [(stamps[i] - stamps[i - 1]) * 1000 for i in range(1, len(stamps))],
    }


def test_streaming_is_incremental_and_paced(stub):
    r = _drain_sse(stub)
    assert r["content"] == 40
    assert r["done"], "stream must terminate with [DONE]"
    med = sorted(r["gaps"])[len(r["gaps"]) // 2]
    # 200 tokens/sec => 5 ms between tokens.
    assert 3.0 < med < 9.0, f"pacing wrong: median inter-token gap {med:.2f} ms"
    # If frames were buffered and released at once this would be near-instant;
    # that would make T_upstream fictional and the firewall tax meaningless.
    assert r["total_ms"] > 190, f"stream completed in {r['total_ms']:.0f} ms — buffered, not streamed"


def test_streaming_supports_connection_reuse(stub):
    """The load harness needs keep-alive; this is why chunked framing is used
    rather than Connection: close."""
    first = _drain_sse(stub)
    second = _drain_sse(stub)
    assert first["content"] == second["content"] == 40
    assert first["done"] and second["done"]


def test_health_reports_configuration(stub):
    h = _wait_health(stub)
    assert h["status"] == "ok"
    assert h["tokens"] == 40
