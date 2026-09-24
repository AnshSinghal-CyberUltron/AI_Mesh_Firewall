"""Client disconnect cancels the upstream request; guard-unready at startup is UNAVAILABLE."""

from __future__ import annotations

import os
import socket
import time

import httpx
import pytest

from tests.e2e.conftest import KEY_A, KEY_B, MODEL, records_for, rid


def _metric_count(base: str, name: str) -> int:
    return int(httpx.get(f"{base}/metrics/all", timeout=10).json()["count"].get(name, 0))


def test_client_disconnect_aborts_the_provider_stream(base: str) -> None:
    before = _metric_count(base, "client_disconnects")
    r = rid("disconnect")
    host, port = base.split("//")[1].split(":")
    body = (b'{"model":"%s","stream":true,"max_tokens":300,"messages":[{"role":"user","content":"hi"}]}'
            % MODEL.encode())
    s = socket.create_connection((host, int(port)))
    s.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
              b"Authorization: Bearer " + KEY_A.encode() + b"\r\nx-request-id: " + r.encode() +
              b"\r\nx-synth-tokens: 300\r\nx-synth-itl-ms: 20\r\nx-synth-ttft-ms: 10\r\n"
              b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    got = b""
    while got.count(b"data: ") < 5:
        got += s.recv(65536)
    t_close = time.monotonic()
    s.close()  # client goes away mid-stream (a full stream would take >= 6 s)
    deadline = time.monotonic() + 5
    while _metric_count(base, "client_disconnects") <= before and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _metric_count(base, "client_disconnects") > before
    recs = records_for(r, expect=1, timeout=8.0)
    # the provider saw the upstream close: it did NOT produce the 300-token (>= 6 s) stream
    assert len(recs) == 1, recs
    assert int(recs[0]["tokens_out"]) < 300 and int(recs[0]["recv_to_last_ns"]) < 3e9, recs
    assert t_close > 0


@pytest.mark.skipif(os.environ.get("E2E_UNREADY") != "1",
                    reason="run against a stack started with RV_GUARD_WARMUP_DELAY_S>0")
def test_guard_unready_at_startup_is_unavailable_not_clean() -> None:
    from tests.e2e.conftest import BASE as base
    ready = httpx.get(f"{base}/readyz", timeout=10)
    assert ready.status_code == 503 and ready.json()["guard"]["ready"] is False
    for key, want_status, want_disp in ((KEY_A, 403, "BLOCK"), (KEY_B, 200, "ALLOW")):
        resp = httpx.post(f"{base}/v1/chat/completions", timeout=30,
                          headers={"authorization": f"Bearer {key}"},
                          json={"model": MODEL, "max_tokens": 3,
                                "messages": [{"role": "user", "content": "benign"}]})
        assert resp.status_code == want_status and resp.headers["x-rv-disposition"] == want_disp
        assert "sem:U" in resp.headers["x-rv-stages"]
