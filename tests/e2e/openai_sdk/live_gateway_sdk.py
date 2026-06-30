#!/usr/bin/env python3
"""Live OpenAI SDK e2e against the running gateway (:8300).

Provisions the per-org simulator-default API key via control (:8100), then drives
the stock ``openai`` Python SDK over real HTTP (not ASGITransport).

Gate:
  cd gateway && ./.venv/bin/python ../tests/e2e/openai_sdk/live_gateway_sdk.py

Env:
  CONTROL_URL  default http://127.0.0.1:8100
  GATEWAY_URL  default http://127.0.0.1:8300
  TEST_EMAIL   default admin@zeroshield.io
  TEST_PASSWORD default Adm1n!Pass#2024
  SIM_MODEL    default gemma-free
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import openai

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
MODEL = os.environ.get("SIM_MODEL", "gemma-free")
FORBIDDEN_MODEL = "gpt-4-forbidden-sdk-live-e2e"


def _json_request(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def _mint_token() -> str:
    status, body = _json_request("POST", f"{CONTROL}/api/auth/token/", {"email": EMAIL, "password": PASSWORD})
    if status != 200 or not body.get("access"):
        raise SystemExit(f"auth token mint failed: HTTP {status} {body!r}")
    return body["access"]


def _simulator_key(access: str) -> str:
    status, body = _json_request(
        "POST",
        f"{CONTROL}/api/gateways/simulator-default/",
        {},
        {"Authorization": f"Bearer {access}"},
    )
    key = body.get("key")
    if status != 200 or not key:
        raise SystemExit(f"simulator-default key failed: HTTP {status} {body!r}")
    return key


def main() -> int:
    print(f"control={CONTROL} gateway={GATEWAY} model={MODEL}")
    token = _mint_token()
    api_key = _simulator_key(token)
    client = openai.OpenAI(base_url=f"{GATEWAY}/v1", api_key=api_key, max_retries=0)

    # (a) benign chat — must parse as ChatCompletion with request id on wire
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "What is the capital of France? Reply with one word."}],
        max_tokens=32,
    )
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        print("FAIL: empty model content on benign chat")
        return 1
    print(f"OK benign chat content={content[:80]!r}")

    # (b) blocked injection — typed SDK error + request_id
    try:
        client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": INJECTION}],
        )
        print("FAIL: injection was not blocked")
        return 1
    except openai.APIStatusError as exc:
        if exc.status_code < 400:
            print(f"FAIL: expected 4xx block, got {exc.status_code}")
            return 1
        if not exc.request_id:
            print("FAIL: blocked error missing request_id")
            return 1
        if not exc.code:
            print(f"FAIL: blocked error missing code (body={exc.body!r})")
            return 1
        if not exc.type:
            print("FAIL: blocked error missing type")
            return 1
        if not exc.message:
            print("FAIL: blocked error missing message")
            return 1
        if exc.status_code != 400:
            print(f"FAIL: injection block expected HTTP 400, got {exc.status_code}")
            return 1
        if exc.code != "content_filter":
            print(f"FAIL: injection block expected code content_filter, got {exc.code!r}")
            return 1
        print(
            f"OK block status={exc.status_code} code={exc.code!r} type={exc.type!r} "
            f"request_id={exc.request_id!r}"
        )
    except openai.OpenAIError as exc:
        print(f"FAIL: unexpected OpenAI error type: {exc!r}")
        return 1

    # (c) forbidden model — typed 403/404 + request_id
    try:
        client.chat.completions.create(
            model=FORBIDDEN_MODEL,
            messages=[{"role": "user", "content": "hi"}],
        )
        print("FAIL: forbidden model was not rejected")
        return 1
    except openai.APIStatusError as exc:
        if exc.status_code not in (403, 404):
            print(f"FAIL: forbidden model expected 403/404, got {exc.status_code}")
            return 1
        if not exc.request_id or not exc.code or not exc.type or not exc.message:
            print(f"FAIL: forbidden-model error missing typed fields: {exc!r}")
            return 1
        print(
            f"OK forbidden model status={exc.status_code} code={exc.code!r} "
            f"request_id={exc.request_id!r}"
        )

    # (d) stream — chunks must terminate cleanly
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Say hello in three words."}],
        stream=True,
        max_tokens=32,
    )
    chunks = [c for c in stream]
    streamed = "".join(
        (ch.choices[0].delta.content or "")
        for ch in chunks
        if ch.choices and ch.choices[0].delta
    )
    if not streamed.strip():
        print("FAIL: empty streamed content")
        return 1
    print(f"OK stream chunks={len(chunks)} content={streamed[:80]!r}")

    print("live_gateway_sdk: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
