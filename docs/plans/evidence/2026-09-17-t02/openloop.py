#!/usr/bin/env python3
"""Open-loop load against T02 gateway ALLOW path. Honest numbers only. Never prints keys."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

KEYS = Path("/tmp/t02.keys.json")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")
ADMIN = os.environ.get("T02_ADMIN_URL", "http://127.0.0.1:18081")
EVIDENCE = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/plans/evidence/2026-09-17-t02")
RATE = float(os.environ.get("T02_OPENLOOP_RPS", "20"))
DURATION = float(os.environ.get("T02_OPENLOOP_SECONDS", "15"))
PROMPT = "Reply with the single word pong. T02OPENLOOP=1"


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))]


def one(api_key: str) -> dict:
    payload = json.dumps(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 16}
    ).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            return {"http_status": resp.status, "ms": ms, "bytes": len(raw)}
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        code = getattr(exc, "code", None)
        return {"http_status": code, "ms": ms, "error": str(exc)[:120]}


def main():
    keys = json.loads(KEYS.read_text())
    api_key = keys["orgs"]["v3a02-block"]["raw"]
    admin = ""
    secrets = Path("/tmp/t02-secrets.env")
    if secrets.exists():
        for line in secrets.read_text().splitlines():
            if line.startswith("T02_ADMIN_TOKEN="):
                admin = line.split("=", 1)[1].strip().strip('"')
    if admin:
        req = urllib.request.Request(
            f"{ADMIN}/reset",
            data=b"{}",
            method="POST",
            headers={"X-T02-Admin": admin, "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass

    n = max(1, int(RATE * DURATION))
    interval = 1.0 / RATE if RATE > 0 else 0.05
    results = []
    start = time.perf_counter()

    def launch(i):
        target = start + i * interval
        delay = target - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        return one(api_key)

    with ThreadPoolExecutor(max_workers=min(64, n)) as pool:
        futs = [pool.submit(launch, i) for i in range(n)]
        for fut in as_completed(futs):
            results.append(fut.result())
    wall = time.perf_counter() - start
    statuses = {}
    lat = [r["ms"] for r in results if r.get("http_status") == 200]
    for r in results:
        k = str(r.get("http_status"))
        statuses[k] = statuses.get(k, 0) + 1
    recorder_count = None
    if admin:
        req = urllib.request.Request(f"{ADMIN}/calls", headers={"X-T02-Admin": admin})
        try:
            body = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            recorder_count = body.get("count")
        except Exception:
            recorder_count = None
    out = {
        "mode": "open_loop",
        "target_rps": RATE,
        "duration_s": DURATION,
        "scheduled": n,
        "completed": len(results),
        "wall_s": round(wall, 3),
        "achieved_rps": round(len(results) / wall, 3) if wall else None,
        "http_200": statuses.get("200", 0),
        "status_hist": statuses,
        "latency_ms": {
            "n": len(lat),
            "p50": pct(lat, 0.50),
            "p95": pct(lat, 0.95),
            "p99": pct(lat, 0.99),
        },
        "recorder_calls": recorder_count,
        "not_a_1064_claim": True,
        "not_a_20ms_sla_claim": True,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / "l02_openloop.json"
    blob = json.dumps(out, indent=2)
    if api_key in blob:
        raise SystemExit("REFUSING to write evidence containing API key")
    path.write_text(blob + "\n")
    print(json.dumps({"path": str(path), "achieved_rps": out["achieved_rps"], "http_200": out["http_200"], "p99_ms": out["latency_ms"]["p99"]}))


if __name__ == "__main__":
    main()
