#!/usr/bin/env python3
"""Token-emitting OpenAI-compatible upstream stub for the nine-stage E2E harness.

Task 1.1 of `.kiro/specs/hot-path-latency-20ms/tasks.md`.

WHY THIS EXISTS
---------------
The firewall tax is `T_total - T_upstream`. Measuring it needs an upstream whose
timing we control, so the number is the gateway's and not a provider's. But a
*non-emitting* stub is worse than useless: with no tokens the output path never
runs, so `output_guardrail` reports 0 ms, `full_nine_stages` is False, and the
harness silently measures a six-stage pipeline while calling it nine.

This stub therefore emits **real tokens at a controlled rate** so the output
guard, per-chunk Tier-1 and the SSE machinery all execute — and it **refuses to
start** if configured to emit zero (Requirement 7.7).

HONESTY
-------
Completion ids are prefixed `chatcmpl-e2estub-` so the capacity gate can
recognise the upstream as a stub. Runs against this stub are valid for the
*latency tax* claim and invalid for a *capacity* claim; the harness enforces that
by setting `STUB_LLM=1`.

Stdlib only — no dependency beyond CPython, so the container image is trivial.

Usage
-----
    python token_stub.py --port 9099 --tokens 400 --rate 200

Env equivalents: STUB_PORT, STUB_TOKENS, STUB_RATE_TPS, STUB_TTFT_MS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Prose with enough lexical variety that redaction/scan passes do real work.
# Deliberately benign: this stub must never itself trip a detector.
_CORPUS = (
    "The deployment pipeline validates each artifact before promoting it to the "
    "next environment, recording the digest and the reviewer who approved it. "
    "Downstream consumers read the manifest, resolve their dependencies, and "
    "report readiness through the health endpoint. When a rollout is paused the "
    "controller drains connections gracefully so that in-flight requests finish "
    "rather than failing. Operators review the summary, confirm the metrics look "
    "steady, and either continue the rollout or revert to the previous revision. "
).split()

_COUNTER = threading.Lock()
_SERVED = 0


def _tokens(n: int) -> list[str]:
    return [_CORPUS[i % len(_CORPUS)] + " " for i in range(n)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "aimesh-e2e-token-stub/1.0"

    # Silence per-request logging; it would distort timing under load.
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "served": _SERVED,
                             "tokens": self.server.cfg["tokens"]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "unsupported path"}})
            return

        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json(400, {"error": {"message": "malformed request body"}})
            return

        global _SERVED
        with _COUNTER:
            _SERVED += 1
            seq = _SERVED

        cfg = self.server.cfg
        cid = f"chatcmpl-e2estub-{seq}-{uuid.uuid4().hex[:8]}"
        model = req.get("model") or "e2e-token-stub"
        toks = _tokens(cfg["tokens"])
        gap = 1.0 / cfg["rate"] if cfg["rate"] > 0 else 0.0

        if req.get("stream"):
            self._stream(cid, model, toks, gap, cfg["ttft_ms"])
        else:
            self._once(cid, model, toks, gap, cfg["ttft_ms"])

    def _once(self, cid, model, toks, gap, ttft_ms) -> None:
        # Simulate generation wall-clock so T_upstream is realistic and the
        # firewall tax is a genuine subtraction rather than an artefact.
        time.sleep(ttft_ms / 1000.0 + gap * len(toks))
        text = "".join(toks)
        self._json(200, {
            "id": cid, "object": "chat.completion", "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(toks),
                      "total_tokens": len(toks)},
        })

    def _stream(self, cid, model, toks, gap, ttft_ms) -> None:
        # HTTP/1.1 with keep-alive requires a framing that tells the client where
        # the body ends. SSE has no Content-Length, so we MUST use chunked
        # transfer encoding — BaseHTTPRequestHandler does not add it for us.
        # Without this the client blocks until timeout (observed in test T3).
        # Chunked is preferred over `Connection: close` because the load harness
        # needs connection reuse.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")   # nginx must not buffer SSE
        self.end_headers()

        created = int(time.time())

        def sse(delta: dict, finish=None) -> bytes:
            return b"data: " + json.dumps({
                "id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }).encode() + b"\n\n"

        def emit(payload: bytes) -> None:
            """One SSE frame as one HTTP chunk, flushed immediately."""
            self.wfile.write(b"%X\r\n" % len(payload) + payload + b"\r\n")
            self.wfile.flush()

        try:
            time.sleep(ttft_ms / 1000.0)
            emit(sse({"role": "assistant"}))
            for t in toks:
                if gap:
                    time.sleep(gap)
                emit(sse({"content": t}))
            emit(sse({}, finish="stop"))
            emit(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")          # terminal chunk
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected mid-stream. Normal under load; not an error.
            self.close_connection = True


def main() -> int:
    p = argparse.ArgumentParser(description="Token-emitting OpenAI-compatible stub")
    p.add_argument("--port", type=int, default=int(os.getenv("STUB_PORT", "9099")))
    p.add_argument("--tokens", type=int, default=int(os.getenv("STUB_TOKENS", "400")),
                   help="tokens per completion (must be > 0)")
    p.add_argument("--rate", type=float, default=float(os.getenv("STUB_RATE_TPS", "200")),
                   help="tokens/sec; 0 = emit as fast as possible")
    p.add_argument("--ttft-ms", type=float, default=float(os.getenv("STUB_TTFT_MS", "20")),
                   help="simulated time to first token")
    a = p.parse_args()

    # Requirement 7.7: a silently-empty upstream makes the harness measure a
    # six-stage pipeline and report it as nine. Refuse rather than mislead.
    if a.tokens <= 0:
        print(f"FATAL: --tokens must be > 0, got {a.tokens}. A non-emitting stub "
              f"would leave output_guardrail at 0 ms and make full_nine_stages "
              f"False while the harness reported a nine-stage result.",
              file=sys.stderr)
        return 2

    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    srv.daemon_threads = True
    srv.cfg = {"tokens": a.tokens, "rate": a.rate, "ttft_ms": a.ttft_ms}
    print(f"[token-stub] :{a.port} tokens={a.tokens} rate={a.rate}/s "
          f"ttft={a.ttft_ms}ms", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
