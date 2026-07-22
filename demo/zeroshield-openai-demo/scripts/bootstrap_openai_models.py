#!/usr/bin/env python3
"""Reconnect OpenAI org models to a valid provider API key (dev bootstrap).

Use when Chat returns provider_auth_failed / HTTP 401 and gateway logs show
``Incorrect API key`` for gpt-* models — often caused by a mistaken paste of the
admin password into Model Connections instead of a real OpenAI key.

Requires OPENAI_API_KEY in the environment (host .env is loaded by docker compose
into the control/gateway containers). Updates every active OpenAI LLMModelConfig
for the signed-in org via the control API.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CONTROL = os.environ.get("CONTROL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()


def _req(method: str, path: str, token: str = "", body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            detail = json.loads(payload)
        except json.JSONDecodeError:
            detail = payload
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    if not OPENAI_KEY:
        raise SystemExit(
            "OPENAI_API_KEY is not set. Add it to the repo root .env, then recreate gateway/control:\n"
            "  docker compose up -d gateway control"
        )

    tok = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    access = tok["access"]

    listed = _req("GET", "/api/firewall/models/", token=access)
    items = listed if isinstance(listed, list) else listed.get("results", [])

    updated: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("provider") or "").lower() != "openai":
            continue
        model_id = item.get("id")
        name = str(item.get("model_name") or model_id)
        if not model_id:
            continue
        _req(
            "PATCH",
            f"/api/firewall/models/{model_id}/",
            token=access,
            body={"api_key": OPENAI_KEY},
        )
        updated.append(name)

    if not updated:
        print("No OpenAI model connections found to update.")
        return

    print(f"Reconnected OpenAI provider key for: {', '.join(updated)}")
    print("Gateway will hot-reload model configs from Redis (model_reload).")


if __name__ == "__main__":
    main()
