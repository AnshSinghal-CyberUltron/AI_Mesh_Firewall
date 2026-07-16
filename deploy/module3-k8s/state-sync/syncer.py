"""Pull Module 3 quota snapshot → PUT into OPA data store."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
ORG_SLUG = os.environ.get("ORGANIZATION_SLUG", "zeroshield")
OPA_URL = os.environ.get("OPA_URL", "http://module3-opa:8181").rstrip("/")
POLL_SEC = float(os.environ.get("SYNC_POLL_SECONDS", "5"))


def http_json(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def sync_once() -> None:
    if not AGENT_API_KEY:
        raise RuntimeError("AGENT_API_KEY required")
    snap = http_json(
        "GET",
        f"{CONTROL_URL}/api/module3/ingest/quota-snapshot/?organization_slug={ORG_SLUG}",
        headers={"Authorization": f"Bearer {AGENT_API_KEY}"},
    )
    quotas = snap.get("quotas") or {}
    # OPA PUT /v1/data/module3/quotas expects the document body as the value
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/module3/quotas",
        data=json.dumps(quotas).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()
    print("sync ok tenants=", list(quotas.keys()), flush=True)


def main() -> None:
    print("state-sync listening control=", CONTROL_URL, "opa=", OPA_URL, flush=True)
    while True:
        try:
            sync_once()
        except Exception as exc:  # noqa: BLE001
            print("sync error", exc, flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
