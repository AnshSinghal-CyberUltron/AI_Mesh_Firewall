#!/usr/bin/env python3
"""T-C6 HTTP: admin probe SELECT pg_sleep(6) must 504 within ~6s; worker lives."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
CONTAINER = os.environ.get("CONTROL_CONTAINER", "ai_mesh_firewall-control-1")
OUT = os.environ.get("TC6_OUT", "mcp-parallel/findings/phase0a/tc6.json")


def login() -> str:
    payload = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{CONTROL}/api/auth/token/",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access"]


def main() -> int:
    token = login()
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{CONTROL}/api/security/analytics-timeout-probe/",
        headers={"Authorization": f"Bearer {token}"},
    )
    status = None
    body = ""
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", "replace")
    dt = time.perf_counter() - t0

    health = urllib.request.urlopen(f"{CONTROL}/api/health/", timeout=10)
    health_body = health.read().decode()
    inspect = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "-f", "{{json .State}}", CONTAINER],
            text=True,
        )
    )
    if isinstance(inspect, str):
        inspect = json.loads(inspect)

    parsed = {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {"raw": body[:300]}

    timeout_ok = status == 504 and parsed.get("error") == "query_timeout"
    fast_enough = 4.0 <= dt <= 12.0
    worker_live = health.status == 200 and "ok" in health_body and inspect.get("Status") == "running"
    ok = timeout_ok and fast_enough and worker_live
    report = {
        "ok": ok,
        "status": status,
        "dt_s": round(dt, 3),
        "body": parsed,
        "timeout_ok": timeout_ok,
        "fast_enough": fast_enough,
        "worker_live": worker_live,
        "health": health_body,
        "oom_killed": bool(inspect.get("OOMKilled")),
        "status_running": inspect.get("Status"),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
