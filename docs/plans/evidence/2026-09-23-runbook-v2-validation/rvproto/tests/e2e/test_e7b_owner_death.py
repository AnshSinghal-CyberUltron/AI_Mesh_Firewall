"""E7b (owner topology only): the guard OWNER PROCESS is SIGKILLed mid-load. Workers' guard calls
come back UNAVAILABLE -> org-a FAIL_CLOSED BLOCK / org-b FAIL_OPEN proceed + recorded; never a
clean result; /readyz reports not-ready during the outage; the launcher restarts the owner and
every worker reconnects by itself. Skipped unless /readyz reports an owner-topology guard; the
owner must run on this host (local stack)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time

import httpx
import pytest
import redis

from tests.e2e.conftest import REDIS_URL
from tests.e2e.test_e7_guard_down import _burst


def _ready_workers(base: str, workers: int, probes: int) -> tuple[set[int], set[int], set[str]]:
    """(ready workers, not-ready workers, owner pids) seen over `probes` fresh connections."""
    ready: set[int] = set()
    unready: set[int] = set()
    pids: set[str] = set()
    for _ in range(probes):
        with httpx.Client(base_url=base, timeout=10) as c:
            d = c.get("/readyz").json()
        (ready if d["ready"] else unready).add(d["worker"])
        pids.update(re.findall(r"pid=(\d+)", d["guard"]["detail"]))
        if len(ready) >= workers and not unready:
            break
    return ready, unready, pids


def test_owner_process_killed_mid_load_then_restarted(base: str) -> None:
    d = httpx.get(f"{base}/readyz", timeout=10).json()
    if "(owner)" not in d["guard"]["backend"]:
        pytest.skip(f"guard topology is not owner ({d['guard']['backend']})")
    workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
    pid = int(re.search(r"pid=(\d+)", d["guard"]["detail"]).group(1))  # type: ignore[union-attr]
    outage: dict[str, object] = {}

    async def scenario() -> tuple[list, list, list]:  # type: ignore[type-arg]
        before = await _burst(base, "own-pre", 10)
        load = asyncio.ensure_future(_burst(base, "own-mid", 30))
        await asyncio.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        outage["killed_at"] = time.monotonic()
        mid = await load
        after = await _burst(base, "own-down", 10)
        outage["unready"] = (await asyncio.to_thread(_ready_workers, base, workers, 8 * workers))[1]
        return before, mid, after

    before, mid, after = asyncio.run(scenario())
    for x in before:
        assert "sem:E" in str(x["stages"]) and x["disp"] == "ALLOW", x
    unavailable = []
    for x in mid + after:
        stages = str(x["stages"])
        if "sem:E" in stages:  # the guard really ran (before the kill or after the restart)
            assert x["disp"] == "ALLOW", x
            continue
        assert "sem:U" in stages, x  # ... or UNAVAILABLE, routed through the owning rule
        unavailable.append(x)
        if x["org"] == "a":
            assert x["status"] == 403 and x["disp"] == "BLOCK", x
        else:
            assert x["status"] == 200 and x["disp"] == "ALLOW", x
    assert len(unavailable) >= 10, f"the kill produced no outage: {len(unavailable)} UNAVAILABLE"
    assert outage["unready"], "no worker reported not-ready while its owner was dead"
    rds = redis.Redis.from_url(REDIS_URL)
    recs = {json.loads(f[b"r"])["request_id"]: json.loads(f[b"r"])
            for org in ("org-a", "org-b") for _, f in rds.xrange(f"rv:audit:{org}")
            if json.loads(f[b"r"])["phase"] == "input"}
    for x in unavailable:
        rec = recs[x["rid"]]
        assert rec["unavailable_detectors"] == ["injection.pg2"]
        assert rec["deciding_rules"] == (["A.inj.sem"] if x["org"] == "a" else [])
    # the launcher restarts the owner; every worker reconnects without a restart of its own
    end = time.monotonic() + 120
    while True:
        ready, unready, pids = _ready_workers(base, workers, 8 * workers)
        if len(ready) >= workers and not unready and pids and str(pid) not in pids:
            break
        assert time.monotonic() < end, (ready, unready, pids)
        time.sleep(0.5)
    again = asyncio.run(_burst(base, "own-restarted", 5))
    assert all("sem:E" in str(x["stages"]) and x["disp"] == "ALLOW" for x in again), again
    m = httpx.get(f"{base}/metrics/all", timeout=10).json()
    assert [o["pid"] for o in m["owners"]] != [pid] and m["count"].get("guard_owner_disconnects", 0) >= workers
