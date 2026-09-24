"""P7 per-request plan pinning on the LIVE 4-worker rvproto.
  concurrent  200 concurrent SSE requests (output email injected by the provider, random TTFT up to
              1.5 s) while the org's plan flips every 100 ms between REDACT (odd versions) and MONITOR
              (even versions), unique version strings. Every response must be consistent with the
              version in its x-rv-plan-version header, and input+output audit records must agree.
  reuse       a version string is re-used with different content (control-plane version counter
              reset, the case v1's policy_sync documents): State.inspector is keyed (org, version)
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time

import httpx
import redis

from pc import BASE, MODEL, log, r

ORG = "org-pin"
KEY = "sk-rv-org-pin-0001"
OUT_EMAIL = "alice.smith@example.com"  # devprov x-synth-inject: email
IN_EMAIL = "alice.canary@example.com"


def rule(rid: str, cat: str, dets: list[str], mode: str, action: str, prio: int, scope: str) -> dict:
    return {"rule_id": rid, "category": cat, "detectors": dets, "mode": mode, "action": action,
            "priority": prio, "scope": scope, "on_unavailable": {"kind": "fail_closed"}, "threshold": None}


def doc(version: str, pii_in: str, pii_out: str) -> dict:
    mode = lambda m: ("enforce", "redact") if m == "redact" else ("monitor", "redact")  # noqa: E731
    return {"org_id": ORG, "version": version, "streaming_mode": "incremental", "rules": [
        rule("P.secret.in", "secret", ["secret.*"], "enforce", "block", 20, "input"),
        rule("P.pii.in", "pii", ["pii.*"], *mode(pii_in), 30, "input"),
        rule("P.pii.out", "pii", ["pii.*"], *mode(pii_out), 30, "output")]}


def publish(d: dict, rr: redis.Redis | None = None) -> None:
    rr = rr or r()
    p = rr.pipeline()
    p.set("rv:plan:" + ORG, json.dumps(d))
    p.hset("rv:plan_versions", ORG, d["version"])
    p.publish("rv:plan:updates", ORG)
    p.execute()


def ensure_key() -> None:
    import hashlib
    r().hset("rv:keys", hashlib.sha256(KEY.encode()).hexdigest(),
             json.dumps({"key_id": "key-pin", "org_id": ORG, "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    r().set("rv:budget:" + ORG, 10**13)


async def one_stream(i: int, res: list) -> None:
    await asyncio.sleep(random.uniform(0, float(__import__("os").environ.get("STAGGER", "0"))))
    rid = f"pin-{i}-{random.getrandbits(32):08x}"
    hdr = {"authorization": f"Bearer {KEY}", "x-request-id": rid, "x-synth-inject": "email",
           "x-synth-ttft-ms": str(random.randint(0, 1500)), "x-synth-itl-ms": "20", "x-synth-tokens": "20"}
    body = {"model": MODEL, "stream": True, "max_tokens": 20, "messages": [{"role": "user", "content": "tell me"}]}
    text = []
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        async with c.stream("POST", "/v1/chat/completions", json=body, headers=hdr) as resp:
            ver = resp.headers.get("x-rv-plan-version")
            status = resp.status_code
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line.strip() != "data: [DONE]":
                    ch = json.loads(line[6:])
                    for choice in ch.get("choices", []):
                        text.append(choice.get("delta", {}).get("content") or "")
    res.append({"rid": rid, "status": status, "version": ver, "raw_email_out": OUT_EMAIL in "".join(text)})


async def concurrent() -> None:
    ensure_key()
    publish(doc("pc-1", "monitor", "redact"))
    await asyncio.sleep(1.5)
    stop = asyncio.Event()
    pushes: list[tuple[float, str]] = []

    async def flipper() -> None:
        v = 1
        while not stop.is_set():
            v += 1
            ver = f"pc-{v}"
            publish(doc(ver, "monitor", "redact" if v % 2 else "monitor"))
            pushes.append((time.time(), ver))
            await asyncio.sleep(0.1)

    res: list = []
    fl = asyncio.create_task(flipper())
    await asyncio.gather(*(one_stream(i, res) for i in range(200)))
    stop.set()
    await fl
    await asyncio.sleep(2.0)
    bad = []
    for x in res:
        n = int(x["version"].split("-")[1]) if x["version"] else -1
        expect_redacted = n % 2 == 1
        if x["status"] != 200 or (expect_redacted and x["raw_email_out"]):
            bad.append(x)
    rr = r()
    audit_mismatch = 0
    rows: dict[str, list] = {}
    for _, f in rr.xrange("rv:audit:" + ORG):
        rec = json.loads(f[b"r"])
        rows.setdefault(rec["request_id"], []).append(rec)
    checked = 0
    for x in res:
        rs = rows.get(x["rid"], [])
        if len(rs) == 2:
            checked += 1
            if len({q["plan_version"] for q in rs}) != 1 or rs[0]["plan_version"] != x["version"]:
                audit_mismatch += 1
    versions_seen = sorted({x["version"] for x in res}, key=lambda s: int(s.split("-")[1]))
    log("concurrent pinning result", requests=len(res), plan_pushes_during_run=len(pushes),
        distinct_versions_pinned=len(versions_seen), inconsistent_responses=len(bad), examples=bad[:3],
        audit_pairs_checked=checked, audit_input_output_version_mismatch=audit_mismatch,
        raw_email_on_monitor_versions=sum(1 for x in res if x["raw_email_out"]))


def json_call(tag: str) -> dict:
    rid = f"reuse-{tag}-{random.getrandbits(32):08x}"
    with httpx.Client(base_url=BASE, timeout=30) as c:  # fresh connection: any worker
        resp = c.post("/v1/chat/completions",
                      headers={"authorization": f"Bearer {KEY}", "x-request-id": rid, "x-synth-inject": "email"},
                      json={"model": MODEL, "max_tokens": 20,
                            "messages": [{"role": "user", "content": f"please mail {IN_EMAIL} today"}]})
    content = resp.json()["choices"][0]["message"]["content"] if resp.status_code == 200 else resp.text[:100]
    return {"status": resp.status_code, "plan_version": resp.headers.get("x-rv-plan-version"),
            "input_disposition": resp.headers.get("x-rv-disposition"), "output_disposition": resp.headers.get("x-rv-output"),
            "raw_email_in_output": OUT_EMAIL in content}


def sweep_json(tag: str, n: int = 16) -> dict:
    out: dict = {}
    for _ in range(n):
        k = json.dumps(json_call(tag), sort_keys=True)
        out[k] = out.get(k, 0) + 1
    return {json.loads(k)["plan_version"] + " " + k: v for k, v in out.items()}


def reuse() -> None:
    ensure_key()
    publish(doc("r-1", "monitor", "monitor"))
    time.sleep(1.5)
    log("R0 org publishes r-1: input PII MONITOR, output PII MONITOR (every worker serves it)",
        responses=sweep_json("r1-old"))
    publish(doc("r-2", "redact", "redact"))
    time.sleep(1.5)
    log("R1 tenant enables PII redaction: r-2 input REDACT + output REDACT", responses=sweep_json("r2"))
    publish(doc("r-1", "redact", "redact"))  # the version counter restarted: SAME string, NEW content
    time.sleep(1.5)
    log("R2 control plane re-publishes the CURRENT policy (REDACT/REDACT) under a re-used version r-1",
        store_doc=json.loads(r().get("rv:plan:" + ORG))["rules"][1:], responses=sweep_json("r1-new"))


if __name__ == "__main__":
    if sys.argv[1] == "concurrent":
        asyncio.run(concurrent())
    else:
        reuse()
