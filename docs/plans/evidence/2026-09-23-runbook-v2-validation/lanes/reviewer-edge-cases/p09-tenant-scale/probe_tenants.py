"""P09: 10,000+ tenants — per-worker plan snapshot cost (startup, memory, reconcile on the event loop).

rvproto keeps EVERY tenant's compiled plan in every worker, loads them one GET per org at startup
and reconciles by HGETALL of the whole version hash every RV_PLAN_RECONCILE_MS (1 s) and on every
pub/sub push. For N extra tenants: time-to-ready, worker RSS, one-HGETALL cost, event-loop lag and
request latency under light steady traffic, and during a burst of 200 plan pushes.
Run: probe_tenants.py <snapdir> <stackdir> <out.json> N [N ...]
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

import httpx
import redis

SNAP, ST, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
NS = [int(x) for x in sys.argv[4:]]
sys.path.insert(0, SNAP)
from rvproto.plan.fixtures import org_a, org_b  # noqa: E402

R = redis.Redis(port=16479)
UP = f"{ST}/up.sh"
DOWN = f"{ST}/down.sh"


def seed_extra(n: int) -> None:
    pipe = R.pipeline(transaction=False)
    for i in range(n):
        org = f"org-t{i:06d}"
        doc = (org_a if i % 2 else org_b)(version=f"v1-{i}", org_id=org)
        pipe.set("rv:plan:" + org, json.dumps(doc))
        pipe.hset("rv:plan_versions", org, doc["version"])
        if i % 2000 == 1999:
            pipe.execute()
    pipe.execute()


def summary(name: str) -> dict:
    d = json.load(urllib.request.urlopen("http://127.0.0.1:8480/metrics/all"))
    return d["summary"].get(name, {})


def rss_mb() -> int:
    launcher = int(open(f"{ST}/run/rvproto.pid").read().strip())
    pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
    return sum(int(l.split()[1]) for p in pids for l in open(f"/proc/{p}/status") if l.startswith("VmRSS:")) // 1024


def traffic(seconds: float, rps: float) -> list[float]:
    lat = []
    end = time.perf_counter() + seconds
    with httpx.Client(timeout=30) as c:
        while time.perf_counter() < end:
            t = time.perf_counter()
            c.post("http://127.0.0.1:8480/v1/chat/completions",
                   json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-tokens": "2", "x-synth-ttft-ms": "0"})
            lat.append(time.perf_counter() - t)
            time.sleep(max(0.0, 1 / rps - (time.perf_counter() - t)))
    return lat


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1e3, 1)


def run(n: int) -> dict:
    subprocess.run(["bash", DOWN], check=True)
    logdir = f"{os.path.dirname(OUT)}/run-N{n}"
    subprocess.run(["bash", UP, logdir + "-seed"], check=True, capture_output=True)   # flush + base tenants
    seed_extra(n)
    subprocess.run(["bash", DOWN], check=True)
    t0 = time.perf_counter()
    r = subprocess.run(["bash", UP, logdir], env=dict(os.environ, NOSEED="1"), capture_output=True, text=True)
    ready_s = round(time.perf_counter() - t0, 1)
    row = {"extra_tenants": n, "plans_in_store": R.hlen("rv:plan_versions"), "time_to_ready_s": ready_s,
           "up_out": r.stdout.strip()[-80:]}
    t = time.perf_counter()
    R.hgetall("rv:plan_versions")
    row["one_hgetall_ms_client_side"] = round((time.perf_counter() - t) * 1e3, 1)
    row["worker_rss_MiB_ready"] = rss_mb()
    lat = traffic(30, 5)
    ll = summary("loop_lag_ns")
    row.update(steady_req_p50_ms=pct(lat, 0.5), steady_req_p99_ms=pct(lat, 0.99), steady_req_max_ms=pct(lat, 1.0),
               loop_lag_p99_ms=round(ll.get("p99_ms", 0), 2), loop_lag_max_ms=round(ll.get("max_ms", 0), 1),
               plan_reloads=json.load(urllib.request.urlopen("http://127.0.0.1:8480/metrics.json"))["count"].get("plan_reloads"))
    # burst: 200 plan pushes (each is a real version change for a random tenant) while traffic runs
    import random
    import threading
    res: dict = {}
    th = threading.Thread(target=lambda: res.setdefault("lat", traffic(15, 5)))
    th.start()
    time.sleep(2)
    for k in range(200):
        org = f"org-t{random.randrange(max(n, 1)):06d}" if n else "org-b"
        doc = (org_a if k % 2 else org_b)(version=f"v2-{k}", org_id=org)
        R.set("rv:plan:" + org, json.dumps(doc))
        R.hset("rv:plan_versions", org, doc["version"])
        R.publish("rv:plan:updates", org)
        time.sleep(0.01)
    th.join()
    ll2 = summary("loop_lag_ns")
    row.update(burst_req_p50_ms=pct(res["lat"], 0.5), burst_req_p99_ms=pct(res["lat"], 0.99),
               burst_req_max_ms=pct(res["lat"], 1.0), loop_lag_max_ms_after_burst=round(ll2.get("max_ms", 0), 1),
               worker_rss_MiB_end=rss_mb())
    print(json.dumps(row), flush=True)
    return row


if __name__ == "__main__":
    rows = [run(n) for n in NS]
    json.dump(rows, open(OUT, "w"), indent=1)
