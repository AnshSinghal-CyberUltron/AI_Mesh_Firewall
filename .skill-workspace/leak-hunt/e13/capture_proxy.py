"""Leak-hunt capture proxy (§2 — reconstructed from Invariant II + finding G4).

A recording reverse proxy that sits between the gateway and each upstream egress
channel (embedding provider, Pinecone, LLM, MCP servers). It forwards every
request to the real upstream and records the EXACT bytes on the wire — request
(egress) AND response (ingress) — to a per-channel JSONL capture log, each body
sha256'd and coarse-scanned for synthetic PII/secrets.

WHY THIS EXISTS: finding G4 proved the gateway only sets a boolean
`policy_redacted_flag` and hashes only the *original* prompt — it never hashes or
wire-compares the *forwarded* payload. So the gateway cannot, by itself, prove
Invariant II ("attestation byte-verified"). This proxy is the external observer:
the capture log is the ground truth that E14 diffs against the gateway's flag.

The detector here is DELIBERATELY independent of the product scanner (an external
observer must not trust the thing it audits). It only recognizes the synthetic
test vectors in leak-hunt/corpus — it is NOT a production DLP.

Run (LOCAL harness only — see verify_wiring.py; never silently MITM prod):
    cd gateway && .venv/bin/python -m uvicorn \
        --app-dir ../.skill-workspace/leak-hunt capture_proxy:app --port 8900

Then point each provider base-URL at  http://127.0.0.1:8900/cap/<channel>  .
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "capture_config.json").read_text())
CAPTURE_DIR = HERE / "captures"
CAPTURE_DIR.mkdir(exist_ok=True)

# Coarse, harness-local leak detector — SYNTHETIC test vectors only (corpus).
_PATTERNS = {
    "ssn": re.compile(r"\b9\d{2}-\d{2}-\d{4}\b"),          # synthetic 9xx SSNs
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
}
_SENSITIVE_HEADERS = {"authorization", "api-key", "x-api-key", "openai-api-key", "cookie"}


def _scan(body: bytes) -> list[str]:
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return []
    return sorted(k for k, rx in _PATTERNS.items() if rx.search(text))


def _redact_headers(headers) -> dict:
    return {
        k: ("<redacted>" if k.lower() in _SENSITIVE_HEADERS else v)
        for k, v in headers.items()
    }


def record(channel: str, direction: str, method: str, url: str, headers, body: bytes) -> dict:
    """Append one wire-observation to the channel's capture log. Returns the record."""
    rec = {
        "ts": time.time(),
        "channel": channel,
        "direction": direction,          # "request" = egress to upstream, "response" = ingress
        "method": method,
        "url": url,
        "headers": _redact_headers(headers),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_len": len(body),
        "leak_detected": _scan(body),    # synthetic-PII categories seen ON THE WIRE
        "body_preview": body[:2048].decode("utf-8", "replace"),
    }
    with (CAPTURE_DIR / f"{channel}.jsonl").open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


app = FastAPI(title="leak-hunt-capture-proxy")


@app.post("/slow/chat/completions")
async def slow_stream(request: Request):
    """E13 deterministic slow OpenAI SSE stream (12s) so a mid-CONTENT kill-switch
    can be observed truncating the remainder. Records the egress (redacted prompt)
    on the 'slow' channel, then emits 30 content chunks at 0.4s spacing."""
    body = await request.body()
    record("slow", "request", request.method, "/slow/chat/completions", request.headers, body)

    async def gen():
        created = int(time.time())

        def _chunk(delta, finish=None):
            return "data: " + json.dumps({
                "id": "chatcmpl-e13slow", "object": "chat.completion.chunk",
                "created": created, "model": "e13-slow",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }) + "\n\n"

        yield _chunk({"role": "assistant", "content": ""})
        for i in range(1, 31):
            yield _chunk({"content": f"This is sentence number {i}. "})
            await asyncio.sleep(0.4)
        yield _chunk({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "channels": list(CONFIG["channels"].keys()), "capture_dir": str(CAPTURE_DIR)}


@app.api_route("/cap/{channel}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def capture(channel: str, path: str, request: Request):
    ch = CONFIG["channels"].get(channel)
    if not ch:
        return Response(
            json.dumps({"error": f"unknown channel {channel!r}"}),
            status_code=404, media_type="application/json",
        )
    upstream = ch["upstream_base_url"].rstrip("/") + "/" + path
    body = await request.body()
    record(channel, "request", request.method, upstream, request.headers, body)

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    async with httpx.AsyncClient(timeout=ch.get("timeout", 60)) as client:
        up = await client.request(
            request.method, upstream, content=body,
            headers=fwd_headers, params=request.query_params,
        )
    record(channel, "response", request.method, upstream, up.headers, up.content)

    resp_headers = {
        k: v for k, v in up.headers.items()
        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
    }
    return Response(
        up.content, status_code=up.status_code,
        headers=resp_headers, media_type=up.headers.get("content-type"),
    )
