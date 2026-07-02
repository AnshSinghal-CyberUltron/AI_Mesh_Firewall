#!/usr/bin/env python3
"""P9 item #28 — concurrency correctness of the 15-MCP fleet under burst load.

Distinct from the item-27 driver (mcp_scale_matrix_live.py, which fires one
echo+get-sum per target per round): this harness fires ``CONCURRENCY`` requests
*per MCP simultaneously* — so each per-org sandbox's stdio agent receives many
in-flight JSON-RPC calls at once, stressing its ``_pending``-dict multiplexer
(the exact code where a response could be delivered to the wrong waiter, dropped,
or two calls' results swapped).

Every request is stamped with BOTH a unique JSON-RPC ``id`` and a unique nonce
embedded in the echo message. Two INDEPENDENT oracles must hold on every reply:

  1. id-match:   response.id  == the id we sent   → no waiter mix-up / no drop
  2. nonce-match: echoed text contains OUR nonce  → no content mixing

Because nonces are globally unique, a mixed/swapped response carries a *different*
nonce and fails oracle 2 even if ids happened to line up; because ids are unique,
a swapped waiter fails oracle 1 even if content matched. get-sum(a,b) adds a
deterministic arithmetic oracle. Cross-tenant: an org's reply must never carry
another org's nonce.

PASS iff, across all rounds: drops==0, id_mismatch==0, content_mismatch==0,
arith_mismatch==0, cross_tenant==0, and received==expected.

Env: SCALE_MANIFEST, ROUNDS (default 6), CONCURRENCY (per-target, default 8).
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
ROUNDS = int(os.environ.get("ROUNDS", "6"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))  # in-flight requests PER MCP

_mf = json.load(open(MANIFEST, encoding="utf-8"))
GATEWAY = _mf.get("gateway", "http://127.0.0.1:8300").rstrip("/")
ORGS = _mf["orgs"]


async def _call(client, org, server, key, tool, args):
    """One tool call. Returns the sent id + parsed reply so oracles can compare."""
    req_id = f"{org}:{server}:{tool}:{uuid.uuid4().hex}"
    payload = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
               "params": {"name": tool, "arguments": args}}
    url = f"{GATEWAY}/gateway/{org}/mcp/{server}"
    t0 = time.perf_counter()
    try:
        r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}, timeout=90.0)
        ms = (time.perf_counter() - t0) * 1000.0
        body = r.json()
        content = (body.get("result") or {}).get("content") or []
        text = content[0].get("text", "") if content else json.dumps(body.get("error") or body)
        return {"ok_http": r.status_code == 200, "status": r.status_code,
                "req_id": req_id, "resp_id": body.get("id"), "text": text, "ms": ms}
    except Exception as exc:  # noqa: BLE001 — a drop/timeout is a real finding, not fatal
        return {"ok_http": False, "status": -1, "req_id": req_id, "resp_id": None,
                "text": f"EXC:{exc}", "ms": (time.perf_counter() - t0) * 1000.0}


async def _warmup(client, targets) -> int:
    """Bounded per-target readiness wait — one echo, retried until it echoes back.

    Separates concurrency correctness (this item) from cold-start readiness
    (item #29/B3): a tool call landing before the server finishes ``initialize``
    returns an id-matched 200 whose content is NOT the echo. We warm first so the
    measured burst reflects steady-state multiplexing, not provisioning.
    """
    warmed = 0
    for org, server, key in targets:
        for _ in range(15):
            probe = f"warm|{org}|{server}|{uuid.uuid4().hex[:8]}"
            res = await _call(client, org, server, key, "echo", {"message": probe})
            if res["ok_http"] and probe in res["text"]:
                warmed += 1
                break
            await asyncio.sleep(1.0)
    return warmed


async def main() -> int:
    targets = [(o["slug"], s, o["gateway_key"]) for o in ORGS for s in o["servers"]]
    slugs = {o["slug"] for o in ORGS}
    n = len(targets)
    inflight = n * CONCURRENCY * 2  # echo + get-sum
    print(f"concurrency harness: {len(ORGS)} orgs x {len(ORGS[0]['servers'])} servers = {n} MCPs; "
          f"{CONCURRENCY}/MCP → {inflight} in-flight/round; {ROUNDS} rounds")

    drops = id_mismatch = content_mismatch = arith_mismatch = cross_tenant = 0
    received = expected = 0
    lat: list[float] = []
    per_org_ok = {s: 0 for s in slugs}

    limits = httpx.Limits(max_connections=max(64, inflight), max_keepalive_connections=64)
    async with httpx.AsyncClient(limits=limits) as client:
        warmed = await _warmup(client, targets)
        print(f"  warmup: {warmed}/{n} MCPs ready")
        if warmed != n:
            print("CONCURRENCY: FAIL (warmup incomplete)")
            return 1
        for rnd in range(ROUNDS):
            tasks = []
            meta = []  # parallel list: (org, server, kind, expect)
            for org, server, key in targets:
                for i in range(CONCURRENCY):
                    nonce = f"{org}|{server}|r{rnd}|c{i}|{uuid.uuid4().hex[:10]}"
                    tasks.append(_call(client, org, server, key, "echo", {"message": nonce}))
                    meta.append((org, server, "echo", nonce))
                    a, b = rnd * 100 + i, (i * 7 + 3)
                    tasks.append(_call(client, org, server, key, "get-sum", {"a": a, "b": b}))
                    meta.append((org, server, "sum", str(a + b)))
            results = await asyncio.gather(*tasks)

            for (org, server, kind, expect), res in zip(meta, results):
                expected += 1
                if not res["ok_http"]:
                    drops += 1
                    continue
                received += 1
                lat.append(res["ms"])
                # oracle 1 — JSON-RPC id round-trips exactly (no waiter mix-up)
                if res["resp_id"] != res["req_id"]:
                    id_mismatch += 1
                text = res["text"]
                if kind == "echo":
                    # oracle 2 — our exact unique nonce came back (no content mixing)
                    if expect not in text:
                        content_mismatch += 1
                    # cross-tenant — no OTHER org's nonce in this org's reply
                    for other in slugs - {org}:
                        if f"{other}|{server}|" in text:
                            cross_tenant += 1
                else:  # get-sum arithmetic oracle
                    if expect not in text:
                        arith_mismatch += 1
                if (kind == "echo" and expect in text) or (kind == "sum" and expect in text):
                    if res["resp_id"] == res["req_id"]:
                        per_org_ok[org] += 1
            print(f"  round {rnd}: recv={received} drops={drops} id_mm={id_mismatch} "
                  f"content_mm={content_mismatch} arith_mm={arith_mismatch} xtenant={cross_tenant}")

    report = {
        "orgs": len(ORGS), "mcps": n, "concurrency_per_mcp": CONCURRENCY, "rounds": ROUNDS,
        "expected": expected, "received": received, "drops": drops,
        "id_mismatch": id_mismatch, "content_mismatch": content_mismatch,
        "arith_mismatch": arith_mismatch, "cross_tenant": cross_tenant,
        "latency_ms": {"p50": round(statistics.median(lat), 1) if lat else None,
                       "p99": round(sorted(lat)[int(len(lat) * 0.99)], 1) if len(lat) > 1 else None,
                       "max": round(max(lat), 1) if lat else None},
        "per_org_ok": per_org_ok,
    }
    print(json.dumps(report, indent=2))
    ok = (drops == 0 and id_mismatch == 0 and content_mismatch == 0
          and arith_mismatch == 0 and cross_tenant == 0 and received == expected)
    print("CONCURRENCY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
