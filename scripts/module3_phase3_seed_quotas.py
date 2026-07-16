#!/usr/bin/env python3
"""Seed Module 3 Phase 3 demo quota policies for Kind/OPA e2e."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")


def http_json(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def main() -> None:
    tok = http_json("POST", "/api/auth/token/", {"email": EMAIL, "password": PASSWORD})["access"]
    policies = [
        {
            "tenant_id": "acme",
            "environment": "prod",
            "tokens_per_minute": 1000,
            "tokens_per_day": 100_000,
            "enabled": True,
        },
        {
            "tenant_id": "acme",
            "environment": "dev",
            "tokens_per_minute": 10,
            "tokens_per_day": 100,
            "enabled": True,
        },
    ]
    for p in policies:
        out = http_json("POST", "/api/module3/api-governance/policies/", p, token=tok)
        print("policy", out.get("tenant_id"), out.get("environment"), "tpm=", out.get("tokens_per_minute"))
    print("=== Module 3 Phase 3 quotas seeded ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("FAIL:", exc, file=sys.stderr)
        raise SystemExit(1)
