"""In-process smoke proving the capture proxy records wire bytes + flags synthetic PII.

No network, no prod: the upstream httpx call is faked, so this only exercises the
record/scan/forward path. Proves the byte-capture substrate works before any
provider base-URL is repointed.

    cd gateway && .venv/bin/python ../.skill-workspace/leak-hunt/smoke_capture.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_proxy as cp
from fastapi.testclient import TestClient


class _FakeResp:
    status_code = 200
    content = b'{"data":[{"embedding":[0.1,0.2,0.3]}]}'
    headers = {"content-type": "application/json"}


class _FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def request(self, *a, **k): return _FakeResp()


def main() -> int:
    cp.httpx.AsyncClient = _FakeClient  # fake the upstream — no real egress
    log = cp.CAPTURE_DIR / "embedding-egress.jsonl"
    before = log.stat().st_size if log.exists() else 0

    client = TestClient(cp.app)
    fixture = {"model": "byok-embed", "input": "embed: patient Jane Roe SSN 900-12-3456 jane.roe@example.com"}
    r = client.post("/cap/embedding-egress/embeddings", json=fixture)
    assert r.status_code == 200, r.status_code

    new = [json.loads(line) for line in log.read_text().splitlines()][-2:]
    req = next(x for x in new if x["direction"] == "request")
    resp = next(x for x in new if x["direction"] == "response")

    ok = (
        log.stat().st_size > before
        and "ssn" in req["leak_detected"]
        and "email" in req["leak_detected"]
        and len(req["body_sha256"]) == 64
        and resp["direction"] == "response"
    )
    print("request.leak_detected :", req["leak_detected"])
    print("request.body_sha256   :", req["body_sha256"][:16], "...")
    print("response captured     :", resp["body_len"], "bytes,", resp["body_sha256"][:16], "...")
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
