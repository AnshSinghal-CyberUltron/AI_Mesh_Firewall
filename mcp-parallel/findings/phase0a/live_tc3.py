#!/usr/bin/env python3
"""T-C3: 60-min dashboard-cadence soak + recycle-count drill.

Recycling is leak insurance. It is not the OOM fix (C-1′ SQL pushdown is).
Gunicorn --max-requests fires AFTER a request completes; an in-request OOM
is not prevented by this flag.

Success (plan §3.3):
  RSS plateaus; recycle count matches max-requests; zero OOM.
Fail:
  monotonic RSS growth, or recycling that produces user-visible 5xx.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
CONTAINER = os.environ.get("CONTROL_CONTAINER", "ai_mesh_firewall-control-1")
REPO = Path(__file__).resolve().parents[3]
OUT_DIR = Path(os.environ.get("TC3_OUT_DIR", str(REPO / "mcp-parallel/findings/phase0b")))

# Overview: startVisibleInterval 10s, four heavies, HEAVY_ANALYTICS_CONCURRENCY=2.
POLL_S = float(os.environ.get("TC3_POLL_S", "10"))
DURATION_S = int(os.environ.get("TC3_DURATION_S", "3600"))
PERIOD = os.environ.get("TC3_PERIOD", "24h")
CONCURRENCY = int(os.environ.get("TC3_CONCURRENCY", "2"))
RELOGIN_S = int(os.environ.get("TC3_RELOGIN_S", "900"))
SAMPLE_EVERY_POLLS = int(os.environ.get("TC3_SAMPLE_EVERY_POLLS", "1"))
WARMUP_S = float(os.environ.get("TC3_WARMUP_S", "60"))
PLATEAU_SPREAD_KB = int(os.environ.get("TC3_PLATEAU_SPREAD_KB", str(80 * 1024)))
RECYCLE_MAX_REQUESTS = int(os.environ.get("TC3_RECYCLE_MAX_REQUESTS", "30"))

HEAVY = (
    f"/api/security/soc-kpis/?period={PERIOD}",
    f"/api/security/attack-vector-trends/?period={PERIOD}",
    f"/api/security/module-kpis/?period={PERIOD}",
    f"/api/security/module-trends/?period={PERIOD}",
)

NOTE_NOT_OOM_FIX = (
    "Recycling is leak insurance, not an OOM fix. "
    "SQL pushdown (C-1') is the OOM fix."
)


def expected_worker_recycles(handled: int, max_requests: int, jitter: int = 0) -> tuple[int, int]:
    """Inclusive (min, max) gunicorn worker recycles for one worker.

    Gunicorn recycles after ``max_requests + randint(0, jitter)`` requests.
    """
    if max_requests <= 0 or handled <= 0:
        return (0, 0)
    hi_threshold = max_requests + max(0, int(jitter))
    lo_threshold = max_requests
    return (handled // hi_threshold, handled // lo_threshold)


def parse_max_requests_from_cmdline(cmdline: str) -> tuple[int, int]:
    parts = cmdline.split()
    max_requests = 0
    jitter = 0
    for i, part in enumerate(parts):
        if part == "--max-requests" and i + 1 < len(parts):
            max_requests = int(parts[i + 1])
        if part == "--max-requests-jitter" and i + 1 < len(parts):
            jitter = int(parts[i + 1])
    return max_requests, jitter


def _exec(*args: str) -> str:
    return subprocess.check_output(["docker", "exec", CONTAINER, *args], text=True)


def gunicorn_cmdline() -> str:
    return _exec("sh", "-c", 'tr "\\0" " " < /proc/1/cmdline').strip()


def worker_snapshot() -> dict:
    script = r"""
import os, json
workers = []
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    try:
        comm = open(f"/proc/{pid}/comm").read().strip()
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
        rss = 0
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
                break
    except OSError:
        continue
    if comm == "gunicorn" and "UvicornWorker" in cmd and pid != 1:
        workers.append({"pid": pid, "rss_kb": rss})
print(json.dumps(workers))
"""
    workers = json.loads(_exec("python", "-c", script).strip() or "[]")
    rss = [int(w["rss_kb"]) for w in workers]
    pids = [int(w["pid"]) for w in workers]
    return {
        "pids": pids,
        "rss_kb": rss,
        "max_rss_kb": max(rss) if rss else 0,
        "sum_rss_kb": sum(rss),
        "n": len(workers),
    }


def oom_kill_count() -> int:
    events = _exec("cat", "/sys/fs/cgroup/memory.events")
    for line in events.splitlines():
        if line.startswith("oom_kill "):
            return int(line.split()[1])
    return 0


def cgroup_current() -> int:
    return int(_exec("cat", "/sys/fs/cgroup/memory.current"))


def login() -> str:
    payload = json.dumps({"email": EMAIL, "password": PASSWORD}).encode()
    last: Exception | dict | None = None
    for attempt in range(6):
        req = urllib.request.Request(
            f"{CONTROL}/api/auth/token/",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())["access"]
        except urllib.error.HTTPError as exc:
            last = {"status": exc.code, "body": exc.read()[:160]}
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last = exc
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"login failed: {last}")


def fetch(token: str | None, path: str, timeout: int = 30) -> dict:
    t0 = time.perf_counter()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", headers=headers)

    def _one() -> dict:
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
                "snippet": raw[:160].decode("utf-8", "replace"),
            }
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            return {
                "path": path,
                "status": 0,
                "bytes": 0,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": type(exc).__name__,
                "snippet": str(exc)[:160],
            }

    result = _one()
    if int(result.get("status") or 0) in (0, 502, 503):
        time.sleep(0.4)
        result = _one()
        result["retried"] = True
    return result


def wait_healthy(timeout_s: float = 120) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        result = fetch(None, "/api/health/", timeout=5)
        last = result
        if int(result.get("status") or 0) == 200:
            return
        time.sleep(1)
    raise RuntimeError(f"control not healthy: {last}")


def poll_round(token: str) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = [pool.submit(fetch, token, path, 30) for path in HEAVY]
        for fut in as_completed(futs):
            results.append(fut.result())
    return results


def rss_plateau(samples: list[dict], warmup_s: float = WARMUP_S) -> dict:
    warmed = [s for s in samples if float(s.get("t", 0)) >= warmup_s]
    series = [int(s.get("max_rss_kb") or 0) for s in (warmed or samples)]
    if not series:
        return {"ok": False, "reason": "no_rss_samples", "spread_kb": 0}
    spread = max(series) - min(series)
    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
    strictly_up = bool(deltas) and all(d >= 512 for d in deltas)
    ok = spread <= PLATEAU_SPREAD_KB and not strictly_up
    return {
        "ok": ok,
        "spread_kb": spread,
        "min_kb": min(series),
        "max_kb": max(series),
        "strictly_monotonic_up": strictly_up,
        "limit_kb": PLATEAU_SPREAD_KB,
    }


def _compose_recreate(**env: str) -> None:
    merged = os.environ.copy()
    merged.update(env)
    subprocess.check_call(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "control"],
        cwd=str(REPO),
        env=merged,
    )
    wait_healthy(180)


def run_recycle_drill() -> dict:
    """Force max-requests low enough that workers recycle; then restore 2000."""
    before_cmd = gunicorn_cmdline()
    oom_before = oom_kill_count()
    _compose_recreate(
        GUNICORN_MAX_REQUESTS=str(RECYCLE_MAX_REQUESTS),
        GUNICORN_MAX_REQUESTS_JITTER="0",
    )
    live_cmd = gunicorn_cmdline()
    max_requests, jitter = parse_max_requests_from_cmdline(live_cmd)
    snap0 = worker_snapshot()
    start_pids = set(snap0["pids"])
    seen = set(start_pids)
    token = login()
    statuses: list[int] = []
    rounds = 0
    deadline = time.time() + 180
    while time.time() < deadline:
        results = poll_round(token)
        rounds += 1
        statuses.extend(int(r.get("status") or 0) for r in results)
        snap = worker_snapshot()
        seen.update(snap["pids"])
        births = seen - start_pids
        if len(births) >= max(1, len(start_pids)):
            break
        time.sleep(0.2)
    # Extra cadence after first recycle wave — catch user-visible 5xx.
    extra_5xx = 0
    for _ in range(6):
        token = login()
        for r in poll_round(token):
            st = int(r.get("status") or 0)
            statuses.append(st)
            if st >= 500 or st == 0:
                extra_5xx += 1
        time.sleep(POLL_S)
    snap1 = worker_snapshot()
    births = seen - start_pids
    visible_5xx = sum(1 for st in statuses if st >= 500 or st == 0)
    handled_approx = rounds * len(HEAVY)
    per_worker = handled_approx // max(1, len(start_pids) or 1)
    exp_lo, exp_hi = expected_worker_recycles(per_worker, max_requests, jitter)
    # Cluster-wide: at least one recycle per starting worker.
    expected_births_lo = max(exp_lo, 1) * max(1, len(start_pids)) if max_requests else 0
    recycle_ok = (
        max_requests == RECYCLE_MAX_REQUESTS
        and jitter == 0
        and len(births) >= len(start_pids)
        and visible_5xx == 0
        and extra_5xx == 0
        and oom_kill_count() - oom_before == 0
    )
    report = {
        "ok": recycle_ok,
        "note": NOTE_NOT_OOM_FIX,
        "before_cmdline": before_cmd,
        "drill_cmdline": live_cmd,
        "max_requests": max_requests,
        "jitter": jitter,
        "start_pids": sorted(start_pids),
        "end_pids": snap1["pids"],
        "seen_pids": sorted(seen),
        "births": sorted(births),
        "n_start_workers": len(start_pids),
        "n_births": len(births),
        "rounds": rounds,
        "handled_approx": handled_approx,
        "per_worker_handled_approx": per_worker,
        "expected_recycles_per_worker": [exp_lo, exp_hi],
        "expected_births_lo": expected_births_lo,
        "visible_5xx_after_retry": visible_5xx,
        "extra_wave_5xx": extra_5xx,
        "oom_kill_delta": oom_kill_count() - oom_before,
        "status_hist": {str(s): statuses.count(s) for s in sorted(set(statuses))},
    }
    _compose_recreate(
        GUNICORN_MAX_REQUESTS="2000",
        GUNICORN_MAX_REQUESTS_JITTER="100",
    )
    restored = gunicorn_cmdline()
    report["restored_cmdline"] = restored
    report["restored_ok"] = "--max-requests 2000" in restored and "--max-requests-jitter 100" in restored
    report["ok"] = bool(report["ok"] and report["restored_ok"])
    return report


def run_soak(duration_s: int = DURATION_S) -> dict:
    wait_healthy(30)
    cmdline = gunicorn_cmdline()
    max_requests, jitter = parse_max_requests_from_cmdline(cmdline)
    oom_before = oom_kill_count()
    token = login()
    last_login = time.time()
    t0 = time.time()
    samples = []
    statuses: list[int] = []
    polls = 0
    handled = 0
    seen_pids: set[int] = set()
    start_pids = set(worker_snapshot()["pids"])
    seen_pids.update(start_pids)
    jsonl = OUT_DIR / "tc3_soak.jsonl"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("")

    while True:
        elapsed = time.time() - t0
        if elapsed >= duration_s:
            break
        if time.time() - last_login >= RELOGIN_S:
            token = login()
            last_login = time.time()
        results = poll_round(token)
        polls += 1
        handled += len(results)
        statuses.extend(int(r.get("status") or 0) for r in results)
        if polls % SAMPLE_EVERY_POLLS == 0:
            snap = worker_snapshot()
            seen_pids.update(snap["pids"])
            sample = {
                "t": round(time.time() - t0, 1),
                "health": int(fetch(None, "/api/health/", timeout=5).get("status") or 0),
                "kpi_statuses": [int(r.get("status") or 0) for r in results],
                "max_rss_kb": snap["max_rss_kb"],
                "sum_rss_kb": snap["sum_rss_kb"],
                "pids": snap["pids"],
                "n_workers": snap["n"],
                "cgroup": cgroup_current(),
                "oom_kill": oom_kill_count(),
            }
            samples.append(sample)
            with jsonl.open("a") as fh:
                fh.write(json.dumps(sample) + "\n")
            print(
                f"TC3_SOAK t={sample['t']:.0f}s rss_max={sample['max_rss_kb']} "
                f"pids={sample['pids']} oom={sample['oom_kill']}",
                flush=True,
            )
        remaining = duration_s - (time.time() - t0)
        time.sleep(max(0.0, min(POLL_S, remaining)))

    n_workers = max(1, len(start_pids) or 1)
    per_worker = handled // n_workers
    exp_lo, exp_hi = expected_worker_recycles(per_worker, max_requests, jitter)
    births = seen_pids - start_pids
    n_births = len(births)
    recycle_match = exp_lo <= n_births <= max(exp_hi + n_workers, exp_hi)
    # Production 2000: expected 0. Allow at most one unrelated worker restart.
    if exp_hi == 0:
        recycle_match = n_births <= 1
    visible_5xx = sum(1 for st in statuses if st >= 500 or st == 0)
    health_ok = all(int(s.get("health") or 0) == 200 for s in samples) if samples else False
    plateau = rss_plateau(samples)
    oom_delta = oom_kill_count() - oom_before
    ok = (
        plateau["ok"]
        and oom_delta == 0
        and visible_5xx == 0
        and recycle_match
        and health_ok
        and max_requests > 0
    )
    return {
        "ok": ok,
        "gate": "T-C3",
        "note": NOTE_NOT_OOM_FIX,
        "duration_s": duration_s,
        "poll_s": POLL_S,
        "period": PERIOD,
        "concurrency": CONCURRENCY,
        "cmdline": cmdline,
        "max_requests": max_requests,
        "jitter": jitter,
        "polls": polls,
        "handled": handled,
        "per_worker_handled_approx": per_worker,
        "n_start_workers": len(start_pids),
        "start_pids": sorted(start_pids),
        "seen_pids": sorted(seen_pids),
        "births": sorted(births),
        "n_births": n_births,
        "expected_recycles_per_worker": [exp_lo, exp_hi],
        "recycle_count_matches_max_requests": recycle_match,
        "visible_5xx_after_retry": visible_5xx,
        "status_hist": {str(s): statuses.count(s) for s in sorted(set(statuses))},
        "oom_kill_delta": oom_delta,
        "plateau": plateau,
        "samples": samples,
        "peak_rss_kb": max((s.get("max_rss_kb") or 0) for s in samples) if samples else 0,
        "baseline_rss_kb": samples[0]["max_rss_kb"] if samples else 0,
    }


def main() -> int:
    mode = os.environ.get("TC3_MODE", "all").lower()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict = {"note": NOTE_NOT_OOM_FIX, "mode": mode}
    rc = 0
    if mode in ("recycle", "all"):
        recycle = run_recycle_drill()
        report["recycle"] = recycle
        (OUT_DIR / "tc3_recycle.json").write_text(json.dumps(recycle, indent=2))
        print(json.dumps({"recycle_ok": recycle.get("ok"), "births": recycle.get("n_births")}))
        if not recycle.get("ok"):
            rc = 1
    if mode in ("soak", "all"):
        soak = run_soak(DURATION_S)
        report["soak"] = soak
        (OUT_DIR / "tc3.json").write_text(json.dumps(soak, indent=2))
        print(
            json.dumps(
                {
                    "soak_ok": soak.get("ok"),
                    "duration_s": soak.get("duration_s"),
                    "plateau": soak.get("plateau"),
                    "n_births": soak.get("n_births"),
                    "oom_kill_delta": soak.get("oom_kill_delta"),
                    "visible_5xx_after_retry": soak.get("visible_5xx_after_retry"),
                }
            )
        )
        if not soak.get("ok"):
            rc = 1
    report["ok"] = rc == 0
    (OUT_DIR / "tc3_full.json").write_text(json.dumps(report, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
