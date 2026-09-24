"""E6: org-a / org-b, identical fixtures, 200 concurrent requests: opposite dispositions,
correct plan versions, zero cross-tenant leakage (config, findings, request ids, audit)."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import redis

from tests.e2e.conftest import (
    CANARY_AWS,
    CANARY_EMAIL,
    INJECTION,
    KEY_A,
    KEY_B,
    MODEL,
    REDIS_URL,
    post_retrying,
    rid,
)

FIXTURES = {
    "benign": ("Summarize the history of maps.", {"a": "ALLOW", "b": "ALLOW"}),
    "pii": (f"Mail {CANARY_EMAIL} the summary.", {"a": "REDACT", "b": "ALLOW"}),
    "secret": (f"Deploy with {CANARY_AWS} today.", {"a": "BLOCK", "b": "ALLOW"}),
    "injection": (INJECTION, {"a": "BLOCK", "b": "FLAG"}),
}
PLAN = {"a": "a-1", "b": "b-1"}
KEYS = {"a": KEY_A, "b": KEY_B}


async def _one(client: httpx.AsyncClient, org: str, kind: str, stream: bool) -> dict[str, object]:
    r = rid(f"{org}-{kind}")
    text = FIXTURES[kind][0]
    resp, sheds = await post_retrying(client, "/v1/chat/completions", headers={
        "authorization": f"Bearer {KEYS[org]}", "x-request-id": r},
        json={"model": MODEL, "stream": stream, "max_tokens": 5,
              "messages": [{"role": "user", "content": text}]})
    body = resp.content
    return {"rid": r, "org": org, "kind": kind, "status": resp.status_code, "stream": stream, "sheds": sheds,
            "echo": resp.headers.get("x-request-id"), "disp": resp.headers.get("x-rv-disposition"),
            "plan": resp.headers.get("x-rv-plan-version"), "stages": resp.headers.get("x-rv-stages"),
            "done": body.endswith(b"data: [DONE]\n\n") if stream and resp.status_code == 200 else None}


def test_two_tenants_200_concurrent(base: str, tmp_path_factory: object) -> None:
    jobs = [(org, kind, i % 2 == 0) for i in range(25) for org in ("a", "b") for kind in FIXTURES]
    assert len(jobs) == 200

    async def run() -> list[dict[str, object]]:
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
        async with httpx.AsyncClient(base_url=base, timeout=120, limits=limits) as c:
            return await asyncio.gather(*(_one(c, *j) for j in jobs))

    t0 = time.monotonic()
    res = asyncio.run(run())
    elapsed = time.monotonic() - t0
    bad = []
    for x in res:
        want = FIXTURES[x["kind"]][1][x["org"]]  # type: ignore[index]
        if x["disp"] != want or x["plan"] != PLAN[x["org"]] or x["echo"] != x["rid"]:  # type: ignore[index]
            bad.append(x)
        if "sem:E" not in str(x["stages"]):
            bad.append(x)
        if want == "BLOCK" and x["status"] != 403 or want != "BLOCK" and x["status"] != 200:
            bad.append(x)
        if x["done"] is False:
            bad.append(x)
    assert not bad, bad[:5]
    assert len({x["rid"] for x in res}) == 200
    _check_audit(res)
    print(json.dumps({"requests": len(res), "elapsed_s": round(elapsed, 2),
                      "overload_sheds_retried": sum(int(x["sheds"]) for x in res)}))


def _check_audit(res: list[dict[str, object]]) -> None:
    rds = redis.Redis.from_url(REDIS_URL)
    by_org = {"a": {x["rid"] for x in res if x["org"] == "a"}, "b": {x["rid"] for x in res if x["org"] == "b"}}
    want = {x["rid"]: x for x in res}
    deadline = time.monotonic() + 10
    while True:
        streams = {o: [json.loads(f[b"r"]) for _, f in rds.xrange(f"rv:audit:org-{o}")] for o in "ab"}
        mine = {o: [s for s in streams[o] if s["request_id"] in want] for o in "ab"}
        expected = sum(1 if x["disp"] == "BLOCK" else 2 for x in res)
        if sum(len(v) for v in mine.values()) >= expected or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    for o, recs in streams.items():
        other = "b" if o == "a" else "a"
        for rec in recs:
            assert rec["org_id"] == f"org-{o}"
            assert rec["request_id"] not in by_org[other], rec
            if rec["request_id"] in want:
                assert rec["plan_version"] == PLAN[o]
    for o in "ab":
        per: dict[str, list[str]] = {}
        for rec in mine[o]:
            per.setdefault(rec["request_id"], []).append(rec["phase"])
        for r in by_org[o]:
            phases = sorted(per.get(r, []))
            blocked = want[r]["disp"] == "BLOCK"
            assert phases == (["input"] if blocked else ["input", "output"]), (r, phases)
        for rec in mine[o]:
            if rec["phase"] != "input":
                continue
            kind = want[rec["request_id"]]["kind"]
            hits = {f["detector"] for f in rec["findings"] if f["spans"]}
            if kind == "pii":
                assert hits == {"pii.email"}, hits
            if kind == "secret":
                assert hits == {"secret.aws"}, hits
            if kind == "benign":
                assert hits == set(), hits
