"""E7: guard backend killed mid-load: semantic findings UNAVAILABLE; FAIL_CLOSED rule (org-a)
-> BLOCK; FAIL_OPEN rule (org-b) -> declared action (proceed) + recorded; never a fabricated
clean result. Then revive and prove recovery. Needs RV_ADMIN_HOOKS=1."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import redis

from tests.e2e.conftest import KEY_A, KEY_B, MODEL, REDIS_URL, post_retrying, rid

N_WORKERS_PROBES = 40


def _hook_all(base: str, action: str, workers: int) -> set[int]:
    """SO_REUSEPORT spreads connections; probe with fresh connections until every worker acted."""
    seen: set[int] = set()
    for _ in range(N_WORKERS_PROBES * workers):
        with httpx.Client(base_url=base, timeout=10) as c:
            d = c.post(f"/_rv/guard/{action}").json()
        seen.add(d["worker"])
        if len(seen) >= workers:
            break
    return seen


def _workers(base: str) -> int:
    d = httpx.get(f"{base}/metrics.json", timeout=10).json()
    return int(d.get("gauge", {}).get("workers", 0)) or 2


async def _burst(base: str, tag: str, n: int) -> list[dict[str, object]]:
    async def one(c: httpx.AsyncClient, org: str, key: str, i: int) -> dict[str, object]:
        r = rid(f"{tag}-{org}-{i}")
        resp, _ = await post_retrying(c, "/v1/chat/completions", headers={"authorization": f"Bearer {key}",
                                                                          "x-request-id": r},
                                      json={"model": MODEL, "max_tokens": 4, "stream": i % 2 == 0,
                                            "messages": [{"role": "user", "content": f"benign question {i}"}]})
        return {"rid": r, "org": org, "status": resp.status_code,
                "disp": resp.headers.get("x-rv-disposition"), "stages": resp.headers.get("x-rv-stages")}

    async with httpx.AsyncClient(base_url=base, timeout=120) as c:
        return await asyncio.gather(*(one(c, org, key, i) for i in range(n)
                                      for org, key in (("a", KEY_A), ("b", KEY_B))))


def test_guard_killed_mid_load_then_revived(base: str) -> None:
    workers = int(__import__("os").environ.get("WEB_CONCURRENCY", "2"))

    async def scenario() -> tuple[list, list, list]:  # type: ignore[type-arg]
        before = await _burst(base, "pre", 10)
        load = asyncio.ensure_future(_burst(base, "mid", 30))
        await asyncio.sleep(0.05)
        killed = await asyncio.to_thread(_hook_all, base, "kill", workers)
        mid = await load
        after = await _burst(base, "down", 10)
        assert killed == set(range(workers)), killed
        return before, mid, after

    before, mid, after = asyncio.run(scenario())
    for x in before:
        assert "sem:E" in str(x["stages"]) and x["disp"] == "ALLOW", x
    for x in mid + after:
        stages = str(x["stages"])
        # never a fabricated clean pass: either the guard really ran (before the kill) ...
        if "sem:E" in stages:
            assert x["disp"] == "ALLOW"
            continue
        # ... or it is UNAVAILABLE and routed through the owning rule's posture
        assert "sem:U" in stages, x
        if x["org"] == "a":
            assert x["status"] == 403 and x["disp"] == "BLOCK", x
        else:
            assert x["status"] == 200 and x["disp"] == "ALLOW", x
    assert all("sem:U" in str(x["stages"]) for x in after)
    rds = redis.Redis.from_url(REDIS_URL)
    recs = {json.loads(f[b"r"])["request_id"]: json.loads(f[b"r"])
            for org in ("org-a", "org-b") for _, f in rds.xrange(f"rv:audit:{org}")
            if json.loads(f[b"r"])["phase"] == "input"}
    for x in after:
        rec = recs[x["rid"]]
        assert rec["unavailable_detectors"] == ["injection.pg2"]
        pg2 = [f for f in rec["findings"] if f["detector"] == "injection.pg2"][0]
        assert pg2["status"] == "unavailable" and pg2["confidence"] is None
        assert rec["deciding_rules"] == (["A.inj.sem"] if x["org"] == "a" else [])
    revived = _hook_all(base, "revive", workers)
    assert revived == set(range(workers))
    time.sleep(0.5)
    again = asyncio.run(_burst(base, "revived", 5))
    assert all("sem:E" in str(x["stages"]) and x["disp"] == "ALLOW" for x in again), again
