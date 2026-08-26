#!/usr/bin/env python3
"""Gunicorn HTTP A/B: dump four Overview heavies × periods with X-Analytics-Source."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
OUT = os.environ.get("TC2_AB_OUT", "mcp-parallel/findings/phase0c/tc2_http_ab.json")

PERIODS = ("1h", "6h", "24h", "7d", "30d")
ENDPOINTS = (
    "/api/security/soc-kpis/?period={p}",
    "/api/security/module-kpis/?period={p}",
    "/api/security/attack-vector-trends/?period={p}",
    "/api/security/module-trends/?period={p}",
)


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


def fetch(token: str, path: str) -> dict:
    req = urllib.request.Request(
        f"{CONTROL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            source = resp.headers.get("X-Analytics-Source", "")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        source = exc.headers.get("X-Analytics-Source", "") if exc.headers else ""
        status = exc.code
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        body = {"_raw": raw[:400].decode("utf-8", "replace")}
    return {"path": path, "status": status, "source": source, "body": body}


def flag() -> str:
    try:
        return subprocess.check_output(
            ["docker", "exec", "ai_mesh_firewall-control-1", "printenv", "ANALYTICS_SERVE_ROLLUPS"],
            text=True,
            timeout=10,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"error:{exc}"


def main() -> int:
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    token = login()
    rows = [fetch(token, ep.format(p=p)) for p in PERIODS for ep in ENDPOINTS]
    invalid = fetch(token, "/api/security/soc-kpis/?period=90d")
    expected_raw = {"1h", "6h"}
    source_ok = True
    for row in rows:
        period = row["path"].split("period=")[-1]
        if row["status"] != 200:
            source_ok = False
            continue
        if period in expected_raw and row["source"] != "raw":
            source_ok = False
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analytics_serve_rollups": flag(),
        "rows": rows,
        "invalid_90d": {"status": invalid["status"], "source": invalid["source"]},
        "source_ok": source_ok,
        "all_200": all(r["status"] == 200 for r in rows),
        "invalid_400": invalid["status"] == 400,
    }
    report["ok"] = report["all_200"] and report["invalid_400"] and source_ok
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "flag": report["analytics_serve_rollups"],
                "all_200": report["all_200"],
                "invalid_400": report["invalid_400"],
                "source_ok": source_ok,
                "sources": {r["path"]: r["source"] for r in rows},
            },
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
