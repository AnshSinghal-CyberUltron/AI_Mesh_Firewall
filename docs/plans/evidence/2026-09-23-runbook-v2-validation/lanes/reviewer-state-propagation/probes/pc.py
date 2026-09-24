"""Probe-client helpers for the isolated rvproto stack (:8493, redis :26379 db 0)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import redis

BASE = "http://127.0.0.1:8493"
REDIS = "redis://127.0.0.1:26379/0"
KEY_A, KEY_B, KEY_Q = "sk-rv-org-a-0001", "sk-rv-org-b-0001", "sk-rv-org-q-0001"
MODEL = "gpt-4o-mini"


def r() -> redis.Redis:
    return redis.Redis.from_url(REDIS)


def log(step: str, **kw: Any) -> None:
    print(json.dumps({"t": round(time.time(), 3), "step": step, **kw}, default=str), flush=True)


def probe(key: str, content: str = "hello", model: str = MODEL, max_tokens: int = 3,
          headers: dict[str, str] | None = None, stream: bool = False, timeout: float = 30) -> httpx.Response:
    """Fresh connection per call => SO_REUSEPORT spreads probes over the workers."""
    h = {"authorization": f"Bearer {key}", **(headers or {})}
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "stream": stream,
                            "messages": [{"role": "user", "content": content}]}
    with httpx.Client(base_url=BASE, timeout=timeout) as c:
        return c.post("/v1/chat/completions", headers=h, json=body)


def outcome(resp: httpx.Response) -> str:
    if resp.status_code == 200:
        return f"200 v={resp.headers.get('x-rv-plan-version')} disp={resp.headers.get('x-rv-disposition')}"
    try:
        return f"{resp.status_code} {resp.json()['error']['code']}"
    except Exception:
        return f"{resp.status_code} {resp.text[:80]}"


def sweep(key: str, n: int = 16, **kw: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for _ in range(n):
        o = outcome(probe(key, **kw))
        out[o] = out.get(o, 0) + 1
    return out


def metrics_all() -> dict[str, Any]:
    return httpx.get(f"{BASE}/metrics/all", timeout=10).json()


def readyz() -> dict[str, Any]:
    r_ = httpx.get(f"{BASE}/readyz", timeout=10)
    return {"status": r_.status_code, **r_.json()}
