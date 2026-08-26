#!/usr/bin/env python3
"""T-C1: in-range analytics windows 2xx; 90d → 400; worker RSS growth < 150 MB."""

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
OUT = os.environ.get("TC1_OUT", "mcp-parallel/findings/phase0b/tc1.json")

PERIODS = ("1h", "6h", "24h", "7d", "30d")
HEAVY = (
    "/api/security/soc-kpis/?period={p}",
    "/api/security/module-kpis/?period={p}",
    "/api/security/attack-vector-trends/?period={p}",
    "/api/security/module-trends/?period={p}",
    "/api/security/rag-pipeline-kpis/?period={p}",
)
DASHBOARD = (
    "/api/dashboard/model-usage/?days={d}",
    "/api/dashboard/risk-distribution/?days={d}",
)


def _exec(*args: str) -> str:
    return subprocess.check_output(["docker", "exec", CONTAINER, *args], text=True)


def worker_rss_kb() -> dict[int, int]:
    script = r"""
import os
out = []
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    try:
        comm = open(f"/proc/{pid}/comm").read().strip()
        cmd = open(f"/proc/{pid}/cmdline","rb").read().replace(b"\0", b" ").decode()
        rss = 0
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
                break
    except OSError:
        continue
    if comm == "gunicorn" and "UvicornWorker" in cmd and pid != 1:
        out.append(f"{pid} {rss}")
print("\n".join(out))
"""
    raw = _exec("python", "-c", script).strip()
    rss = {}
    for line in raw.splitlines():
        pid_s, kb_s = line.split()
        rss[int(pid_s)] = int(kb_s)
    return rss


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


def fetch(token: str, path: str, timeout: int = 30) -> dict:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{CONTROL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {
                "path": path,
                "status": resp.status,
                "bytes": len(body),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return {
            "path": path,
            "status": exc.code,
            "bytes": len(raw),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "snippet": raw[:200].decode("utf-8", "replace"),
        }
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return {
            "path": path,
            "status": 0,
            "bytes": 0,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": type(exc).__name__,
            "snippet": str(exc)[:200],
        }


def main() -> int:
    token = login()
    baseline = worker_rss_kb()
    baseline_max = max(baseline.values()) if baseline else 0
    peak_max = baseline_max
    rows = []

    for period in PERIODS:
        for tmpl in HEAVY:
            path = tmpl.format(p=period)
            row = fetch(token, path)
            now = worker_rss_kb()
            now_max = max(now.values()) if now else 0
            peak_max = max(peak_max, now_max)
            row["rss_max_kb"] = now_max
            rows.append(row)

    for days in (30, 365):
        for tmpl in DASHBOARD:
            path = tmpl.format(d=days)
            row = fetch(token, path)
            now = worker_rss_kb()
            now_max = max(now.values()) if now else 0
            peak_max = max(peak_max, now_max)
            row["rss_max_kb"] = now_max
            rows.append(row)

    rejects = []
    for tmpl in HEAVY:
        path = tmpl.format(p="90d")
        row = fetch(token, path)
        rejects.append(row)

    growth_mb = (peak_max - baseline_max) / 1024.0
    in_range_ok = all(200 <= r["status"] < 300 for r in rows)
    reject_ok = all(r["status"] == 400 for r in rejects)
    rss_ok = growth_mb < 150
    silent_24h = any(
        r["status"] == 200 and '"period": "24h"' in (r.get("snippet") or "")
        for r in rejects
    )
    ok = in_range_ok and reject_ok and rss_ok and not silent_24h
    report = {
        "ok": ok,
        "in_range_ok": in_range_ok,
        "reject_90d_ok": reject_ok,
        "rss_ok": rss_ok,
        "silent_24h": silent_24h,
        "baseline_max_rss_kb": baseline_max,
        "peak_max_rss_kb": peak_max,
        "growth_mb": round(growth_mb, 2),
        "in_range": rows,
        "rejects_90d": rejects,
        "baseline_workers": baseline,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=2)
    print(
        json.dumps(
            {
                "ok": ok,
                "in_range_ok": in_range_ok,
                "reject_90d_ok": reject_ok,
                "growth_mb": report["growth_mb"],
                "rss_ok": rss_ok,
            }
        )
    )
    bad = [r for r in rows if not (200 <= r["status"] < 300)]
    if bad:
        print(json.dumps(bad[:8]))
    if not reject_ok:
        print(json.dumps(rejects))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
