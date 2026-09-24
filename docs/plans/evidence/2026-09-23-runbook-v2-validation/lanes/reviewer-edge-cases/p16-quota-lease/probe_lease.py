"""P16: org token-budget LEASE behaviour with 2 workers (rvproto admit/quota.py).

org-q budget = 2,500 tokens (seed), each request costs ~430 estimated tokens (body/4 + 400 default
output). The lease chunk is contract-derived and larger than the whole budget, so the first worker
to refill takes ALL of it. Then: (1) do requests landing on the OTHER worker get 429 while budget is
still unspent? (2) kill -9 the lease-holding worker and restart: is the unspent lease lost for good?
Each request uses a NEW connection so SO_REUSEPORT spreads them over both workers.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

import httpx
import redis

ST, OUT = sys.argv[1], sys.argv[2]
R = redis.Redis(port=16479)


def one() -> tuple[int, str | None]:
    with httpx.Client(timeout=30) as c:  # fresh connection per request
        r = c.post("http://127.0.0.1:8480/v1/chat/completions",
                   json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"authorization": "Bearer sk-rv-org-q-0001", "x-synth-tokens": "2", "x-synth-ttft-ms": "0"})
        code = (r.json().get("error") or {}).get("code") if r.status_code >= 400 else None
        return r.status_code, code


def held_by_worker() -> dict:
    d = json.load(urllib.request.urlopen("http://127.0.0.1:8480/metrics/all"))
    out = {}
    for w in d.get("per_worker", []):
        g = w.get("gauge", {})
        out[str(w.get("worker"))] = {"pid": w.get("pid"), "lease_held_org_q": g.get('lease_held_tokens{org="org-q"}', 0)}
    return out


def main() -> None:
    res: dict = {"budget_in_store_before": int(R.get("rv:budget:org-q"))}
    seq = [one() for _ in range(12)]
    res["requests"] = [f"{s}:{c}" for s, c in seq]
    time.sleep(1.5)  # metrics dump period
    res["budget_in_store_after"] = int(R.get("rv:budget:org-q"))
    res["lease_held_per_worker"] = held_by_worker()
    holders = [v for v in res["lease_held_per_worker"].values() if v["lease_held_org_q"] > 0]
    if holders:
        victim = holders[0]["pid"]
        res["killed_pid_holding_lease"] = victim
        os.kill(victim, signal.SIGKILL)
        time.sleep(3)
        res["launcher_alive_after_worker_kill"] = subprocess.run(
            ["bash", "-c", f"kill -0 $(cat {ST}/run/rvproto.pid) 2>/dev/null && echo alive || echo dead"],
            capture_output=True, text=True).stdout.strip()
        subprocess.run(["bash", f"{ST}/down.sh"], check=True)
        subprocess.run(["bash", f"{ST}/up.sh", os.path.dirname(OUT) + "/restart"],
                       env=dict(os.environ, NOSEED="1", WEB_CONCURRENCY="2"), check=True, capture_output=True)
        res["budget_in_store_after_restart"] = int(R.get("rv:budget:org-q"))
        res["requests_after_restart"] = [f"{s}:{c}" for s, c in (one() for _ in range(4))]
    print(json.dumps(res, indent=1))
    json.dump(res, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    main()
