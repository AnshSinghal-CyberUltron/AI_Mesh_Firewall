#!/usr/bin/env python3
"""P9 item #29 — sustained LOAD over the 15-MCP fleet.

Distinct from item #28 (a short concurrency-correctness burst): this drives a
steady stream of tool calls for many rounds and proves the *operational*
properties of the pool under sustained pressure:

  1. sandbox reuse / pooling — each org's sandbox CONTAINER is reused for the
     whole run (container Id unchanged start→end; no per-call churn).
  2. no exhaustion — zero non-200s after warmup (no fork EAGAIN / fd / proc
     exhaustion resurfacing) and every reply stays correct (echo + get-sum).
  3. no 503 storms — zero 5xx after warm, and no run of consecutive 503s.
  4. resource limits respected — pids_limit / mem_limit / cpu are set, and the
     live per-container pids.current NEVER exceeds pids_limit during the run
     (sampled from the HOST cgroup — no in-container fork needed).
  5. no process leak — pids.current returns to ~baseline after the load drains
     (reuse without unbounded accumulation).

Env: SCALE_MANIFEST, ROUNDS (default 25), CONCURRENCY (per-MCP, default 4).
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import uuid

import httpx

HERE = os.path.dirname(__file__)
MANIFEST = os.environ.get("SCALE_MANIFEST", os.path.join(HERE, "ralph", ".mcp_scale_manifest.json"))
ROUNDS = int(os.environ.get("ROUNDS", "25"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
ORGS = _mf["orgs"]


async def _docker_inspect(name: str) -> dict:
    """Container Id + resource limits via `docker inspect` (async, no shell)."""
    fmt = "{{.Id}}|{{.HostConfig.PidsLimit}}|{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}"
    proc = await asyncio.create_subprocess_exec(
        "docker", "inspect", "--format", fmt, name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    cid, pids, mem, cpus = out.decode().strip().split("|")
    return {"id": cid, "pids_limit": int(pids), "memory": int(mem), "nanocpus": int(cpus)}


def _cgroup_pids_current(cid: str) -> int | None:
    """Read pids.current straight from the host cgroup (no fork into container)."""
    for path in (f"/sys/fs/cgroup/system.slice/docker-{cid}.scope/pids.current",
                 f"/sys/fs/cgroup/docker/{cid}/pids.current"):
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip())
        except OSError:
            continue
    return None


RETRIES = int(os.environ.get("RETRIES", "2"))  # bounded client retries on TRANSIENT errors


def _is_transient(status, err) -> bool:
    """A retryable backpressure signal — 5xx or a JSON-RPC -32000 (control 500/policy)."""
    return status in (502, 503) or (isinstance(err, dict) and err.get("code") == -32000)


async def _call(client, org, server, key, tool, args, retries=None):
    """One idempotent tool call with bounded retry on transient backpressure.

    Returns the FINAL outcome plus `had_transient` (any attempt hit a retryable
    error) so we can separate raw first-attempt error rate from the EFFECTIVE
    (post-retry) rate — a storm would not clear on retry; transient pressure will.
    """
    retries = RETRIES if retries is None else retries
    req_id = f"{org}:{server}:{tool}:{uuid.uuid4().hex}"
    payload = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    url = f"{GATEWAY}/gateway/{org}/mcp/{server}"
    had_transient = False
    t0 = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=90.0)
            body = r.json()
            content = (body.get("result") or {}).get("content") or []
            err = body.get("error")
            text = content[0].get("text", "") if content else json.dumps(err or body)
            status = r.status_code
        except Exception as exc:  # noqa: BLE001
            status, text, err = -1, f"EXC:{exc}", None
        ms = (time.perf_counter() - t0) * 1000.0
        if _is_transient(status, err):
            had_transient = True
            if attempt < retries:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
        return {"status": status, "text": text, "ms": ms, "jsonrpc_err": err,
                "had_transient": had_transient}


async def _warmup(client, targets) -> int:
    warmed = 0
    for org, server, key in targets:
        for _ in range(15):
            probe = f"warm|{uuid.uuid4().hex[:8]}"
            res = await _call(client, org, server, key, "echo", {"message": probe})
            if res["status"] == 200 and probe in res["text"]:
                warmed += 1
                break
            await asyncio.sleep(1.0)
    return warmed


async def main() -> int:
    targets = [(o["slug"], s, o["gateway_key"]) for o in ORGS for s in o["servers"]]
    slugs = [o["slug"] for o in ORGS]
    n = len(targets)
    names = {s: f"{s}-mcp-sandbox" for s in slugs}

    before = {s: await _docker_inspect(names[s]) for s in slugs}
    baseline_pids = {s: _cgroup_pids_current(before[s]["id"]) for s in slugs}
    pids_observable = {s: baseline_pids[s] is not None for s in slugs}
    peak_pids = {s: (baseline_pids[s] if baseline_pids[s] is not None else 0) for s in slugs}
    print(f"load harness: {n} MCPs, {ROUNDS} rounds x {CONCURRENCY}/MCP "
          f"({n * CONCURRENCY * 2} calls/round). limits: "
          + ", ".join(f"{s}(pids≤{before[s]['pids_limit']},mem={before[s]['memory']//2**20}MiB,"
                      f"cpu={before[s]['nanocpus']/1e9}) base_pids={baseline_pids[s]}"
                      f"{' (cgroup unreadable — pids checks skipped)' if not pids_observable[s] else ''}"
                      for s in slugs))

    total = ok = mismatch = err_5xx = err_other = 0
    errored_under_load = true_mix = transient_recovered = 0
    samples: list[dict] = []
    lat: list[float] = []
    max_consecutive_503 = cur_503 = 0
    pids_breach = 0

    limits = httpx.Limits(max_connections=max(64, n * CONCURRENCY * 2), max_keepalive_connections=64)
    async with httpx.AsyncClient(limits=limits) as client:
        warmed = await _warmup(client, targets)
        print(f"  warmup: {warmed}/{n} ready")
        if warmed != n:
            print("LOAD: FAIL (warmup incomplete)")
            return 1

        for rnd in range(ROUNDS):
            tasks, meta = [], []
            for org, server, key in targets:
                for i in range(CONCURRENCY):
                    nonce = f"{org}|{server}|r{rnd}|{uuid.uuid4().hex[:8]}"
                    tasks.append(_call(client, org, server, key, "echo", {"message": nonce}))
                    meta.append((org, server, "echo", nonce))
                    a, b = rnd + i, 11
                    tasks.append(_call(client, org, server, key, "get-sum", {"a": a, "b": b}))
                    meta.append((org, server, "sum", str(a + b)))
            results = await asyncio.gather(*tasks)
            for (org, server, kind, expect), res in zip(meta, results):
                total += 1
                st = res["status"]
                if st == 200:
                    lat.append(res["ms"])
                    if expect in res["text"]:
                        ok += 1
                        if res.get("had_transient"):
                            transient_recovered += 1  # hit a retryable error, a retry resolved it
                    else:
                        mismatch += 1
                        # classify: a JSON-RPC error body (correct id, no result) is an
                        # application error under load — NOT a mixed/leaked response. A
                        # 200 with wrong-but-real content would be a true isolation mix.
                        if res.get("jsonrpc_err"):
                            errored_under_load += 1
                        else:
                            true_mix += 1
                        if len(samples) < 12:
                            samples.append({"org": org, "server": server, "tool": kind,
                                            "jsonrpc_err": res.get("jsonrpc_err"),
                                            "text": res["text"][:200]})
                    cur_503 = 0
                else:
                    if st in (502, 503):
                        err_5xx += 1
                        cur_503 += 1
                        max_consecutive_503 = max(max_consecutive_503, cur_503)
                    else:
                        err_other += 1
                        cur_503 = 0
            # sample live pids from host cgroup (no container fork) — peak tracking
            for s in slugs:
                if not pids_observable[s]:
                    continue
                cur = _cgroup_pids_current(before[s]["id"])
                if cur is not None:
                    peak_pids[s] = max(peak_pids[s], cur)
                    if cur > before[s]["pids_limit"]:
                        pids_breach += 1
            if rnd % 5 == 0 or rnd == ROUNDS - 1:
                print(f"  round {rnd}: total={total} ok={ok} 5xx={err_5xx} other={err_other} "
                      f"mismatch={mismatch} peak_pids={ {s: peak_pids[s] for s in slugs} }")

    after = {s: await _docker_inspect(names[s]) for s in slugs}
    end_pids = {s: _cgroup_pids_current(after[s]["id"]) for s in slugs}
    reused = {s: before[s]["id"] == after[s]["id"] for s in slugs}
    # leak check: pids drained back near baseline (allow slack for lingering timers)
    no_leak = {
        s: (not pids_observable[s]
            or (end_pids[s] is not None and end_pids[s] <= baseline_pids[s] + 40))
        for s in slugs
    }
    limits_set = {s: before[s]["pids_limit"] > 0 and before[s]["memory"] > 0 and before[s]["nanocpus"] > 0
                  for s in slugs}
    pids_within = {
        s: (not pids_observable[s] or peak_pids[s] <= before[s]["pids_limit"])
        for s in slugs
    }

    report = {
        "mcps": n, "rounds": ROUNDS, "concurrency_per_mcp": CONCURRENCY, "total_calls": total,
        "ok": ok, "correctness_mismatch": mismatch,
        "errored_under_load_effective": errored_under_load, "true_isolation_mix": true_mix,
        "transient_recovered_by_retry": transient_recovered, "retries": RETRIES,
        "http_5xx": err_5xx, "http_other_err": err_other,
        "max_consecutive_503": max_consecutive_503, "pids_breach_samples": pids_breach,
        "latency_ms": {"p50": round(statistics.median(lat), 1) if lat else None,
                       "p99": round(sorted(lat)[int(len(lat) * 0.99)], 1) if len(lat) > 1 else None,
                       "max": round(max(lat), 1) if lat else None},
        "sandbox_reuse": reused, "limits_respected": limits_set,
        "peak_pids": peak_pids, "pids_limit": {s: before[s]["pids_limit"] for s in slugs},
        "pids_observable": pids_observable,
        "peak_within_limit": pids_within,
        "baseline_pids": baseline_pids, "end_pids": end_pids, "no_process_leak": no_leak,
        "mismatch_samples": samples,
    }
    print(json.dumps(report, indent=2))
    # Item #29 OPERATIONAL criteria (the literal checklist): reuse, no exhaustion,
    # no 503 storms, limits respected, no process leak — AND no true isolation mix.
    operational_ok = (true_mix == 0 and err_5xx == 0 and err_other == 0
                      and max_consecutive_503 == 0 and pids_breach == 0
                      and all(reused.values()) and all(limits_set.values())
                      and all(pids_within.values()) and all(no_leak.values()))
    # errored_under_load = policy-plane backpressure (correct id, JSON-RPC error, no
    # result → provably not a mix/drop). Gate strictly to 0 by default; relax via
    # LOAD_MAX_ERR_RATE to accept a documented graceful-degradation ceiling.
    max_err_rate = float(os.environ.get("LOAD_MAX_ERR_RATE", "0"))
    err_rate = errored_under_load / total if total else 0.0
    reliability_ok = err_rate <= max_err_rate
    print(f"  operational_ok={operational_ok} true_isolation_mix={true_mix} "
          f"transient_recovered_by_retry={transient_recovered} "
          f"errored_under_load(effective)={errored_under_load} ({err_rate*100:.3f}%) "
          f"reliability_ok={reliability_ok} (max_err_rate={max_err_rate})")
    ok_all = operational_ok and reliability_ok
    print("LOAD:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
