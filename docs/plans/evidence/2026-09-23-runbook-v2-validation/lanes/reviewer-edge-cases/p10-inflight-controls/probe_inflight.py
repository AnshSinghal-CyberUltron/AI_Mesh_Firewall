"""P10: what an operator control does to a stream that is ALREADY in flight.

For each control, start a 40 s stream for org-a, apply the control ~3 s in, verify the control is
live (a NEW org-a request is refused / uses the new plan), then observe whether the in-flight
stream keeps delivering content to the end.
  C1 org kill-switch engaged   C2 API key revoked (key deleted + auth epoch bump)
  C3 plan tightened (new version published)  -> audit plan_version of the in-flight stream
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import uuid

import aiohttp
import redis

SNAP, OUT = sys.argv[1], sys.argv[2]
sys.path.insert(0, SNAP)
from rvproto.plan.fixtures import org_a  # noqa: E402

R = redis.Redis(port=16479)
BASE = "http://127.0.0.1:8480"
KEY = "sk-rv-org-a-0001"
KH = hashlib.sha256(KEY.encode()).hexdigest()


async def stream(s: aiohttp.ClientSession, rid: str, marks: list) -> dict:
    h = {"authorization": f"Bearer {KEY}", "x-request-id": rid, "x-synth-tokens": "1600",
         "x-synth-itl-ms": "25", "x-synth-ttft-ms": "50"}
    body = {"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "tell a story"}]}
    t0 = time.perf_counter()
    chars_after_control = 0
    chars = 0
    done = False
    buf = b""
    async with s.post(f"{BASE}/v1/chat/completions", json=body, headers=h) as r:
        async for data in r.content.iter_any():
            buf += data
            while b"\n\n" in buf:
                ev, buf = buf.split(b"\n\n", 1)
                if ev == b"data: [DONE]":
                    done = True
                    continue
                d = json.loads(ev[6:])
                for ch in d.get("choices") or []:
                    c = (ch.get("delta") or {}).get("content") or ""
                    chars += len(c)
                    if marks:
                        chars_after_control += len(c)
    return {"rid": rid, "status": r.status, "duration_s": round(time.perf_counter() - t0, 1), "chars": chars,
            "chars_after_control_applied": chars_after_control, "done_seen": done,
            "plan_version_header": r.headers.get("x-rv-plan-version")}


async def new_request(s: aiohttp.ClientSession) -> dict:
    async with s.post(f"{BASE}/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": [
            {"role": "user", "content": "hi"}]}, headers={"authorization": f"Bearer {KEY}", "x-synth-tokens": "2"}) as r:
        t = await r.text()
        return {"status": r.status, "code": (json.loads(t).get("error") or {}).get("code") if r.status >= 400 else None,
                "plan_version": r.headers.get("x-rv-plan-version")}


async def run(label: str, apply, undo) -> dict:
    rid = f"p10-{label}-{uuid.uuid4().hex[:6]}"
    marks: list = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
        st = asyncio.create_task(stream(s, rid, marks))
        await asyncio.sleep(3)
        apply()
        marks.append(time.perf_counter())
        await asyncio.sleep(1.5)  # > kill-switch/plan refresh period (500 ms / 1 s)
        probe = await new_request(s)
        res = await st
    undo()
    await asyncio.sleep(1.5)
    res.update(control=label, new_request_after_control=probe)
    return res


def ks_on() -> None:
    R.hset("rv:killswitch:org", "org-a", "1")


def ks_off() -> None:
    R.hdel("rv:killswitch:org", "org-a")


SAVED = {}


def revoke() -> None:
    SAVED["k"] = R.hget("rv:keys", KH)
    R.hdel("rv:keys", KH)
    R.incr("rv:auth_epoch")


def unrevoke() -> None:
    R.hset("rv:keys", KH, SAVED["k"])
    R.incr("rv:auth_epoch")


def plan_v2() -> None:
    doc = org_a("a-2")
    R.set("rv:plan:org-a", json.dumps(doc))
    R.hset("rv:plan_versions", "org-a", "a-2")
    R.publish("rv:plan:updates", "a-2")


def plan_v1() -> None:
    R.set("rv:plan:org-a", json.dumps(org_a("a-1")))
    R.hset("rv:plan_versions", "org-a", "a-1")
    R.publish("rv:plan:updates", "a-1")


async def main() -> None:
    out = [await run("org_killswitch", ks_on, ks_off),
           await run("key_revoked", revoke, unrevoke),
           await run("plan_tightened_new_version", plan_v2, plan_v1)]
    time.sleep(1)
    rid = out[2]["rid"]
    recs = [json.loads(v[b"r"]) for _, v in R.xrange("rv:audit:org-a", count=100000)]
    out[2]["audit_records_for_stream"] = [{k: r[k] for k in ("phase", "plan_version", "disposition", "verification")}
                                          for r in recs if r["request_id"] == rid]
    for o in out:
        print(json.dumps(o))
    json.dump(out, open(OUT, "w"), indent=1)


if __name__ == "__main__":
    asyncio.run(main())
