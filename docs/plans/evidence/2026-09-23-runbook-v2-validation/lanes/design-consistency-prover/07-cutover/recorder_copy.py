#!/usr/bin/env python3
"""T02 synthetic OpenAI-compatible provider recorder (stdlib only).

Public (container :8080, not published on the host):
  GET  /health
  POST /v1/chat/completions   Bearer T02_RECORDER_KEY

Admin (container :18081, published 127.0.0.1:18081 only):
  GET  /health
  GET  /calls
  POST /reset
  GET  /fault
  POST /fault
Requires X-T02-Admin: T02_ADMIN_TOKEN. Never mounted on nginx :8180 / gateway :8300.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
AKIA_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
MAX_BODY = 1_048_576
MAX_CALLS = 20_000

_lock = threading.Lock()
_calls: list[dict[str, Any]] = []
_seq = 0
_fault: dict[str, Any] = {"mode": "none", "delay_ms": 0, "status": 200}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


RECORDER_KEY = _env("T02_RECORDER_KEY")
ADMIN_TOKEN = _env("T02_ADMIN_TOKEN")
OPENAI_PORT = int(_env("T02_OPENAI_PORT", "8080") or "8080")
ADMIN_PORT = int(_env("T02_ADMIN_PORT", "18081") or "18081")


def _extract_prompt(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    parts: list[str] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _record(path: str, body: Any, raw: bytes, authorized: bool) -> int:
    global _seq
    prompt = _extract_prompt(body)
    with _lock:
        _seq += 1
        seq = _seq
        row = {
            "seq": seq,
            "ts": time.time(),
            "path": path,
            "authorized": authorized,
            "body_bytes": len(raw),
            "stream": bool(isinstance(body, dict) and body.get("stream")),
            "model": (body.get("model") if isinstance(body, dict) else None),
            "prompt": prompt[:4000],
            "contains_ssn": bool(SSN_RE.search(prompt) or SSN_RE.search(raw.decode("utf-8", "replace"))),
            "contains_akia": bool(AKIA_RE.search(prompt) or AKIA_RE.search(raw.decode("utf-8", "replace"))),
        }
        _calls.append(row)
        if len(_calls) > MAX_CALLS:
            del _calls[: len(_calls) - MAX_CALLS]
        return seq


def _completion(model: str, content: str) -> bytes:
    payload = {
        "id": f"t02-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "t02-recorder",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    }
    return json.dumps(payload).encode("utf-8")


def _sse(model: str, content: str) -> bytes:
    chunk = {
        "id": f"t02-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model or "t02-recorder",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
    }
    done = {
        "id": chunk["id"],
        "object": "chat.completion.chunk",
        "created": chunk["created"],
        "model": chunk["model"],
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return (
        f"data: {json.dumps(chunk)}\n\n"
        f"data: {json.dumps(done)}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    role = "openai"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("[t02-recorder] " + (fmt % args) + "\n")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > MAX_BODY:
            return b""
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"))

    def _bearer_ok(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        if not RECORDER_KEY:
            return False
        return auth == f"Bearer {RECORDER_KEY}"

    def _admin_ok(self) -> bool:
        if not ADMIN_TOKEN:
            return False
        return (self.headers.get("X-T02-Admin") or "") == ADMIN_TOKEN

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/"):
            self._json(200, {"status": "ok", "role": self.role, "calls": len(_calls)})
            return
        if self.role != "admin":
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        if not self._admin_ok():
            self._json(401, {"error": {"message": "admin auth required", "type": "auth"}})
            return
        if path == "/calls":
            with _lock:
                snapshot = list(_calls)
            self._json(200, {"count": len(snapshot), "calls": snapshot})
            return
        if path == "/fault":
            with _lock:
                fault = dict(_fault)
            self._json(200, fault)
            return
        self._json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        raw = self._read_body()
        try:
            body = json.loads(raw.decode("utf-8") or "null") if raw else {}
        except json.JSONDecodeError:
            body = None

        if self.role == "admin":
            if not self._admin_ok():
                self._json(401, {"error": {"message": "admin auth required", "type": "auth"}})
                return
            if path == "/reset":
                with _lock:
                    _calls.clear()
                    _fault.update({"mode": "none", "delay_ms": 0, "status": 200})
                self._json(200, {"ok": True, "calls": 0})
                return
            if path == "/fault":
                patch = body if isinstance(body, dict) else {}
                with _lock:
                    if "mode" in patch:
                        _fault["mode"] = str(patch.get("mode") or "none")
                    if "delay_ms" in patch:
                        _fault["delay_ms"] = max(0, int(patch.get("delay_ms") or 0))
                    if "status" in patch:
                        _fault["status"] = int(patch.get("status") or 200)
                    fault = dict(_fault)
                self._json(200, fault)
                return
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})
            return

        if path != "/v1/chat/completions":
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})
            return
        if not self._bearer_ok():
            _record(path, body, raw, False)
            self._json(401, {"error": {"message": "invalid recorder key", "type": "auth"}})
            return

        seq = _record(path, body, raw, True)
        with _lock:
            fault = dict(_fault)
        delay_ms = int(fault.get("delay_ms") or 0)
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        mode = str(fault.get("mode") or "none")
        if mode == "disconnect":
            try:
                self.connection.close()
            except Exception:
                pass
            return
        if mode == "malformed":
            self._send(200, b"{not-json", "application/json")
            return
        if mode == "http_error":
            self._json(int(fault.get("status") or 500), {"error": {"message": "t02 injected fault", "type": "fault"}})
            return

        model = str((body or {}).get("model") or "t02-recorder") if isinstance(body, dict) else "t02-recorder"
        content = f"pong-t02 seq={seq}"
        if isinstance(body, dict) and body.get("stream"):
            self._send(200, _sse(model, content), "text/event-stream")
            return
        self._send(200, _completion(model, content))


class OpenAIHandler(_Base):
    role = "openai"


class AdminHandler(_Base):
    role = "admin"


def serve() -> None:
    if not RECORDER_KEY or not ADMIN_TOKEN:
        raise SystemExit("T02_RECORDER_KEY and T02_ADMIN_TOKEN are required")
    openai = ThreadingHTTPServer(("127.0.0.1", OPENAI_PORT), OpenAIHandler)
    admin = ThreadingHTTPServer(("127.0.0.1", ADMIN_PORT), AdminHandler)
    threading.Thread(target=openai.serve_forever, daemon=True, name="t02-openai").start()
    print(f"t02-recorder openai :{OPENAI_PORT} admin :{ADMIN_PORT}", flush=True)
    try:
        admin.serve_forever()
    except KeyboardInterrupt:
        openai.shutdown()
        admin.shutdown()


if __name__ == "__main__":
    serve()
