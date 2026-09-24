"""P4 quota on the LIVE 4-worker rvproto (contract lease chunk = 51,360 tokens on this stack).
  strand   budget 60,000 tokens; 40 requests x ~230 tokens over fresh connections (all 4 workers)
  lost     (run AFTER a stack restart with NO_SEED=1) what happened to the leased tokens
  gcra     org rate 2 req/s burst 2: fresh connections (spread over workers) vs one keep-alive conn
  blocked  a request the plan BLOCKS (zero provider calls) is still charged its full estimate
"""

from __future__ import annotations

import hashlib
import json
import sys
import time

import httpx

from pc import BASE, MODEL, log, metrics_all, r

import os
ORG = os.environ.get("QORG", "org-qq")
KEY = f"sk-rv-{ORG}-0001"


def setup(rate: float = 1e6, burst: float = 1e6) -> None:
    rr = r()
    doc = {"org_id": ORG, "version": f"{ORG}-1", "streaming_mode": "incremental", "rules": [
        {"rule_id": "Q.secret.in", "category": "secret", "detectors": ["secret.*"], "mode": "enforce",
         "action": "block", "priority": 20, "scope": "input", "on_unavailable": {"kind": "fail_closed"}, "threshold": None},
        {"rule_id": "Q.pii.out", "category": "pii", "detectors": ["pii.*"], "mode": "enforce", "action": "redact",
         "priority": 30, "scope": "output", "on_unavailable": {"kind": "fail_closed"}, "threshold": None}]}
    p = rr.pipeline()
    p.set("rv:plan:" + ORG, json.dumps(doc))
    p.hset("rv:plan_versions", ORG, f"{ORG}-1")
    p.hset("rv:keys", hashlib.sha256(KEY.encode()).hexdigest(),
           json.dumps({"key_id": "key-qq", "org_id": ORG, "rate_per_s": rate, "burst": burst, "epoch": 1}))
    p.publish("rv:plan:updates", ORG)
    p.execute()


def post(c: httpx.Client, max_tokens: int = 200, content: str = "hello there, a short benign prompt") -> httpx.Response:
    return c.post("/v1/chat/completions", headers={"authorization": f"Bearer {KEY}"},
                  json={"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]})


def code(resp: httpx.Response) -> str:
    return "200" if resp.status_code == 200 else f"{resp.status_code} {resp.json()['error']['code']}"


def lease_view() -> dict:
    time.sleep(1.2)  # per-worker metric dumps are 1 s apart
    d = metrics_all()
    per = {w["worker"]: {"held": w["gauge"].get(f'lease_held_tokens{{org="{ORG}"}}', 0)} for w in d["per_worker"]}
    c = d["count"]
    per["all_workers"] = {"granted": c.get(f'lease_granted_tokens{{org="{ORG}"}}', 0),
                          "admitted": c.get(f'quota_admitted_tokens{{org="{ORG}"}}', 0),
                          "rejected": c.get(f'quota_rejected{{org="{ORG}"}}', 0)}
    return per


def strand() -> None:
    setup()
    time.sleep(1.5)
    r().set("rv:budget:" + ORG, 60000)
    res: dict[str, int] = {}
    for _ in range(40):
        with httpx.Client(base_url=BASE, timeout=30) as c:  # fresh connection -> SO_REUSEPORT worker choice
            k = code(post(c))
        res[k] = res.get(k, 0) + 1
    per = lease_view()
    admitted = per["all_workers"]["admitted"]
    held = sum(v["held"] for k, v in per.items() if k != "all_workers")
    log("strand result", budget_set=60000, responses=res, store_remaining=int(r().get("rv:budget:" + ORG)),
        admitted_tokens_total=admitted, stranded_in_worker_leases=held, per_worker=per)


def lost() -> None:
    res: dict[str, int] = {}
    for _ in range(12):
        with httpx.Client(base_url=BASE, timeout=30) as c:
            k = code(post(c))
        res[k] = res.get(k, 0) + 1
    per = lease_view()
    log("after restart", responses=res, store_remaining=int(r().get("rv:budget:" + ORG)),
        held_now=sum(v["held"] for k, v in per.items() if k != "all_workers"), per_worker=per)


def gcra() -> None:
    setup(rate=2.0, burst=2.0)
    r().incr("rv:auth_epoch")  # principals re-read with the new rate
    r().set("rv:budget:" + ORG, 10**12)
    time.sleep(1.5)
    out = {}
    for mode in ("fresh_connection_per_request", "one_keepalive_connection"):
        time.sleep(3)  # let every worker's GCRA state drain
        res: dict[str, int] = {}
        t0 = time.time()
        if mode == "one_keepalive_connection":
            with httpx.Client(base_url=BASE, timeout=30) as c:
                while time.time() - t0 < 3.0:
                    k = code(post(c, 3))
                    res[k] = res.get(k, 0) + 1
        else:
            while time.time() - t0 < 3.0:
                with httpx.Client(base_url=BASE, timeout=30) as c:
                    k = code(post(c, 3))
                res[k] = res.get(k, 0) + 1
        out[mode] = {"window_s": 3.0, "responses": res, "admitted": res.get("200", 0),
                     "org_limit_would_allow": 2 + 2 * 3}
    log("gcra result (org rate_per_s=2, burst=2)", **out)


def blocked() -> None:
    setup()
    time.sleep(1.2)
    r().set("rv:budget:" + ORG, 100000)
    with httpx.Client(base_url=BASE, timeout=30) as c:
        k = code(post(c, 4000, "my key is AKIAQYLPMN5HHHFPZAM2 please store it"))
    log("blocked request", response=k, budget_before=100000, store_remaining=int(r().get("rv:budget:" + ORG)),
        note="the lease took a chunk; see quota_admitted_tokens for the charge", per_worker=lease_view())


if __name__ == "__main__":
    {"strand": strand, "lost": lost, "gcra": gcra, "blocked": blocked}[sys.argv[1]]()
