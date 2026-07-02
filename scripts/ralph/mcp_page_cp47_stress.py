#!/usr/bin/env python3
"""MCP-page Ralph — CP47: high-throughput MCP tool-call LOAD DRIVER.

Extends the round-based P9 harnesses (mcp_concurrency_live.py / mcp_load_live.py)
into a MULTIPROCESS × ASYNC CLOSED-LOOP driver built to push toward the 100k-call /
100k-RPS target across many orgs × sandboxes:

  * WORKERS processes (Ruflo-style fan-out across cores) — a single asyncio loop is
    CPU-bound at some ceiling; N processes multiply aggregate throughput.
  * Each process runs CONN async connections in a CLOSED LOOP that pulls from a
    per-process quota until drained — NO per-round barrier, so the achieved RPS is
    real sustained throughput, not lockstep-round wall-clock.
  * Same wire contract as the P9 harnesses: POST {GATEWAY}/gateway/{org}/mcp/{server}
    with Bearer {gateway_key}; deterministic tools echo(nonce) + get-sum(a,b).
  * Two INDEPENDENT isolation oracles on EVERY reply, unchanged in spirit from #28:
      - content: our exact unique nonce echoes back (no waiter mix / content swap)
      - cross-tenant CANARY: each org plants a unique secret token in its echo; a
        reply from org X must NEVER contain org Y's canary (hard leak oracle).
  * Rich metrics: total calls, wall, achieved RPS, p50/p90/p99/max latency, HTTP
    status histogram, true-drop vs backpressure split, failure rate, leak counts.

Env: SCALE_MANIFEST, TARGET_CALLS (default 2000 smoke), WORKERS (default min(cpu,4)),
     CONN (per-worker in-flight, default 8), DURATION_S (optional: run by time not count),
     MAX_FAIL_RATE (drop-rate gate, default 0.02), OUT (report path).

CP47 = the harness EXISTS + a smoke run proves it drives real calls + measures
correctly + isolation oracles run clean. CP48 cranks it to scale; CP49 fixes
bottlenecks. Never fakes green: a true-drop rate over MAX_FAIL_RATE, any content
swap, or any canary leak fails the run.
"""
from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import random
import statistics
import sys
import time
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.environ.get("SCALE_MANIFEST", os.path.join(HERE, ".mcp_scale_manifest.json"))
TARGET_CALLS = int(os.environ.get("TARGET_CALLS", "2000"))
WORKERS = int(os.environ.get("WORKERS", str(min(os.cpu_count() or 4, 4))))
CONN = int(os.environ.get("CONN", "8"))
DURATION_S = float(os.environ.get("DURATION_S", "0"))  # >0 → run by time, ignore quota
MAX_FAIL_RATE = float(os.environ.get("MAX_FAIL_RATE", "0.02"))
RETRIES = int(os.environ.get("RETRIES", "1"))
OUT = os.environ.get("OUT", os.path.join(HERE, "..", "..", "mcp-parallel", "findings", "mcp-page", "cp47", "report.json"))
MAX_SAMPLES = 25000  # per-worker reservoir cap on latency samples (bounds memory/IPC)

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
ORGS = _mf["orgs"]
# org slug → its unique cross-tenant canary secret (planted in that org's echoes).
# NOTE: the canary map ALWAYS spans every org so the cross-tenant leak oracle stays
# meaningful even when load is filtered to a subset (CP48 single-server isolation).
CANARY = {o["slug"]: f"CANARY-{o['slug']}-{uuid.uuid4().hex[:12]}" for o in ORGS}
# Optional load filters (CP48): restrict the DRIVEN targets to isolate a bottleneck.
ORG_FILTER = [s for s in os.environ.get("ORG_FILTER", "").split(",") if s]
SERVER_FILTER = [s for s in os.environ.get("SERVER_FILTER", "").split(",") if s]
TARGETS = [(o["slug"], s, o["gateway_key"]) for o in ORGS for s in o["servers"]
           if (not ORG_FILTER or o["slug"] in ORG_FILTER)
           and (not SERVER_FILTER or s in SERVER_FILTER)]


def _is_transient(status, err) -> bool:
    return status in (502, 503) or (isinstance(err, dict) and err.get("code") in (-32000, -32603))


async def _one_call(client, org, server, key, tool, args):
    """One tool call with bounded retry on transient backpressure. Returns a
    classified result: ok / backpressure / drop + latency + reply text."""
    req_id = f"{org}:{server}:{tool}:{uuid.uuid4().hex}"
    payload = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    url = f"{GATEWAY}/gateway/{org}/mcp/{server}"
    had_transient = False
    t0 = time.perf_counter()
    for attempt in range(RETRIES + 1):
        try:
            r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=90.0)
            body = r.json()
            err = body.get("error")
            content = (body.get("result") or {}).get("content") or []
            text = content[0].get("text", "") if content else json.dumps(err or body)
            status, resp_id = r.status_code, body.get("id")
        except Exception as exc:  # noqa: BLE001
            status, resp_id, text, err = -1, None, f"EXC:{type(exc).__name__}", None
        ms = (time.perf_counter() - t0) * 1000.0
        if _is_transient(status, err):
            had_transient = True
            if attempt < RETRIES:
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
        return {"status": status, "req_id": req_id, "resp_id": resp_id, "text": text,
                "ms": ms, "err": err, "had_transient": had_transient}


def _classify(kind, expect, res, org):
    """→ (bucket, latency_or_None, oracle_flags). bucket ∈ ok|backpressure|drop."""
    flags = {"id_mismatch": 0, "content_mismatch": 0, "arith_mismatch": 0, "cross_tenant": 0}
    if res["err"] or res["status"] in (502, 503):
        return "backpressure", None, flags
    if res["status"] == -1:
        return "drop", None, flags
    if res["status"] != 200:
        return "backpressure", None, flags
    text = res["text"]
    if res["resp_id"] != res["req_id"]:
        flags["id_mismatch"] = 1
    if expect not in text:
        flags["content_mismatch" if kind == "echo" else "arith_mismatch"] = 1
    # hard cross-tenant leak: this org's reply carries ANOTHER org's canary
    for other, tok in CANARY.items():
        if other != org and tok in text:
            flags["cross_tenant"] = 1
    return "ok", res["ms"], flags


def _worker(proc_idx, quota, out_path):
    """Process entry: run CONN async connections closed-loop until quota/duration done."""
    asyncio.run(_worker_async(proc_idx, quota, out_path))


async def _worker_async(proc_idx, quota, out_path):
    rng = random.Random(1000 + proc_idx)  # deterministic per-worker target selection
    counters = {"made": 0, "ok": 0, "backpressure": 0, "drop": 0, "transient_recovered": 0,
                "id_mismatch": 0, "content_mismatch": 0, "arith_mismatch": 0, "cross_tenant": 0}
    status_hist: dict[str, int] = {}
    lat: list[float] = []
    remaining = {"n": quota}
    deadline = (time.perf_counter() + DURATION_S) if DURATION_S > 0 else None

    def _next():
        if deadline is not None:
            return time.perf_counter() < deadline
        if remaining["n"] <= 0:
            return False
        remaining["n"] -= 1
        return True

    limits = httpx.Limits(max_connections=CONN + 4, max_keepalive_connections=CONN + 4)
    async with httpx.AsyncClient(limits=limits) as client:
        async def conn_loop():
            while _next():
                org, server, key = TARGETS[rng.randrange(len(TARGETS))]
                if rng.random() < 0.5:
                    # echo carries a unique nonce + THIS org's canary secret
                    nonce = f"{org}|{server}|p{proc_idx}|{uuid.uuid4().hex[:10]}|{CANARY[org]}"
                    res = await _one_call(client, org, server, key, "echo", {"message": nonce})
                    kind, expect = "echo", nonce
                else:
                    a, b = rng.randrange(10000), rng.randrange(10000)
                    res = await _one_call(client, org, server, key, "get-sum", {"a": a, "b": b})
                    kind, expect = "sum", str(a + b)
                counters["made"] += 1
                if res["had_transient"]:
                    counters["transient_recovered"] += 1
                status_hist[str(res["status"])] = status_hist.get(str(res["status"]), 0) + 1
                bucket, ms, flags = _classify(kind, expect, res, org)
                counters[bucket] += 1
                for k, v in flags.items():
                    counters[k] += v
                if ms is not None:
                    if len(lat) < MAX_SAMPLES:
                        lat.append(ms)
                    else:  # reservoir replacement — bounded, unbiased sample
                        j = rng.randrange(counters["made"])
                        if j < MAX_SAMPLES:
                            lat[j] = ms

        await asyncio.gather(*[conn_loop() for _ in range(CONN)])

    json.dump({"proc": proc_idx, "counters": counters, "status_hist": status_hist, "lat": lat},
              open(out_path, "w", encoding="utf-8"))


async def _warmup(targets):
    """Bounded per-target readiness wait — one echo retried until it echoes back, so
    the timed run reflects steady-state, not cold-start provisioning."""
    warmed = 0
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=16)
    async with httpx.AsyncClient(limits=limits) as client:
        for org, server, key in targets:
            for _ in range(12):
                probe = f"warm|{org}|{server}|{uuid.uuid4().hex[:8]}"
                res = await _one_call(client, org, server, key, "echo", {"message": probe})
                if res["status"] == 200 and probe in res["text"]:
                    warmed += 1
                    break
                await asyncio.sleep(1.0)
    return warmed


def _pct(sorted_lat, q):
    if not sorted_lat:
        return None
    i = min(len(sorted_lat) - 1, int(len(sorted_lat) * q))
    return round(sorted_lat[i], 1)


def main() -> int:
    n = len(TARGETS)
    print(f"CP47 stress driver: {len(ORGS)} orgs × {len(ORGS[0]['servers'])} servers = {n} MCPs; "
          f"WORKERS={WORKERS} CONN={CONN} → {WORKERS * CONN} in-flight; "
          f"{'DURATION=' + str(DURATION_S) + 's' if DURATION_S > 0 else 'TARGET_CALLS=' + str(TARGET_CALLS)}")

    warmed = asyncio.run(_warmup(TARGETS))
    print(f"  warmup: {warmed}/{n} MCPs ready")
    if warmed != n:
        print(f"CP47: FAIL (warmup incomplete {warmed}/{n})")
        return 1

    tmpdir = os.path.join(os.path.dirname(os.path.abspath(OUT)), "workers")
    os.makedirs(tmpdir, exist_ok=True)
    per = max(1, TARGET_CALLS // WORKERS)
    procs, outs = [], []
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()
    for i in range(WORKERS):
        op = os.path.join(tmpdir, f"w{i}.json")
        outs.append(op)
        p = ctx.Process(target=_worker, args=(i, per, op))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    wall = time.perf_counter() - t0

    agg = {"made": 0, "ok": 0, "backpressure": 0, "drop": 0, "transient_recovered": 0,
           "id_mismatch": 0, "content_mismatch": 0, "arith_mismatch": 0, "cross_tenant": 0}
    status_hist: dict[str, int] = {}
    lat: list[float] = []
    for op in outs:
        try:
            d = json.load(open(op, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for k in agg:
            agg[k] += d["counters"].get(k, 0)
        for s, c in d.get("status_hist", {}).items():
            status_hist[s] = status_hist.get(s, 0) + c
        lat.extend(d.get("lat", []))
    lat.sort()

    made = agg["made"]
    rps = round(made / wall, 1) if wall > 0 else None
    drop_rate = round(agg["drop"] / made, 5) if made else 1.0
    report = {
        "checkpoint": "47", "gateway": GATEWAY, "orgs": len(ORGS), "mcps": n,
        "workers": WORKERS, "conn_per_worker": CONN, "target_calls": TARGET_CALLS,
        "duration_s_mode": DURATION_S, "wall_s": round(wall, 2),
        "calls_made": made, "achieved_rps": rps,
        "ok": agg["ok"], "backpressure": agg["backpressure"], "drops": agg["drop"],
        "drop_rate": drop_rate, "transient_recovered": agg["transient_recovered"],
        "isolation": {"id_mismatch": agg["id_mismatch"], "content_mismatch": agg["content_mismatch"],
                      "arith_mismatch": agg["arith_mismatch"], "cross_tenant": agg["cross_tenant"]},
        "status_hist": status_hist,
        "latency_ms": {"p50": _pct(lat, 0.50), "p90": _pct(lat, 0.90), "p99": _pct(lat, 0.99),
                       "max": round(lat[-1], 1) if lat else None,
                       "mean": round(statistics.fmean(lat), 1) if lat else None},
        "canary_leak": agg["cross_tenant"],
    }
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    json.dump(report, open(OUT, "w", encoding="utf-8"), indent=2)
    print(json.dumps(report, indent=2))

    # CP47 smoke gate: the harness DROVE real calls, MEASURED rps+latency, and the
    # isolation oracles ran CLEAN. True-drop rate must stay under MAX_FAIL_RATE;
    # ANY content swap or canary leak fails hard. Backpressure is reported (CP48/49).
    isolation_clean = (agg["content_mismatch"] == 0 and agg["arith_mismatch"] == 0
                       and agg["id_mismatch"] == 0 and agg["cross_tenant"] == 0)
    ok = (made >= int(TARGET_CALLS * 0.95) or DURATION_S > 0) and rps is not None \
        and drop_rate <= MAX_FAIL_RATE and isolation_clean
    print(f"CP47: {'PASS' if ok else 'FAIL'} — made={made} rps={rps} drop_rate={drop_rate} "
          f"p99={report['latency_ms']['p99']}ms isolation_clean={isolation_clean} "
          f"canary_leak={agg['cross_tenant']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
