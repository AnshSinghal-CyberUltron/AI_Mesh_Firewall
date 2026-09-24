"""Controller spec addendum: GW05 plan push + pinning, GW06 kill-switch scopes, quota lease
counters, round-trip counter, GW19 overload shedding."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import openai
import pytest
import redis

from tests.e2e.conftest import CANARY_EMAIL, KEY_A, KEY_B, KEY_Q, MODEL, REDIS_URL, rid

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
PY = sys.executable


def _tool(name: str, *args: str) -> dict[str, object]:
    out = subprocess.run([PY, str(TOOLS_DIR / name), REDIS_URL, *args], check=True, capture_output=True, text=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def _probe(base: str, key: str, content: str = "hello", model: str = MODEL) -> httpx.Response:
    with httpx.Client(base_url=base, timeout=30) as c:  # fresh connection => SO_REUSEPORT picks a worker
        return c.post("/v1/chat/completions", headers={"authorization": f"Bearer {key}"},
                      json={"model": model, "max_tokens": 3, "messages": [{"role": "user", "content": content}]})


def _sweep(base: str, key: str, n: int = 24, **kw: str) -> list[httpx.Response]:
    return [_probe(base, key, **kw) for _ in range(n)]


def _counts(base: str) -> dict[str, int]:
    return httpx.get(f"{base}/metrics/all", timeout=10).json()["count"]


def _audit_rows(request_id: str, org: str) -> list[dict[str, object]]:
    r = redis.Redis.from_url(REDIS_URL)
    return [json.loads(f[b"r"]) for _, f in r.xrange(f"rv:audit:{org}")
            if json.loads(f[b"r"])["request_id"] == request_id]


def test_plan_push_converges_and_requests_pin_one_version(base: str) -> None:
    long_rid = rid("pinned")
    body = {"model": MODEL, "stream": True, "max_tokens": 60,
            "messages": [{"role": "user", "content": f"mail {CANARY_EMAIL} please"}]}
    hdr = {"authorization": f"Bearer {KEY_B}", "x-request-id": long_rid, "x-synth-itl-ms": "30"}
    with httpx.Client(base_url=base, timeout=60) as c, c.stream("POST", "/v1/chat/completions",
                                                               json=body, headers=hdr) as resp:
        assert resp.headers["x-rv-plan-version"] == "b-1"
        it = resp.iter_bytes()
        next(it)  # the stream is open and pinned to b-1
        pub = _tool("plan_update.py", "org-b", "b-2", "--rule", "B.pii.in", "--mode", "enforce",
                    "--action", "redact")
        t_conv = None
        while time.time() - float(pub["published_at"]) < 5:
            if all(r.headers.get("x-rv-plan-version") == "b-2" for r in _sweep(base, KEY_B, 12)):
                t_conv = time.time() - float(pub["published_at"])
                break
        rest = b"".join(it)  # finish the pinned stream AFTER the plan changed
    assert rest.endswith(b"data: [DONE]\n\n")
    assert t_conv is not None and t_conv < 2.0, t_conv  # push (+ reconcile 1 s) bound
    redacted = _probe(base, KEY_B, content=f"mail {CANARY_EMAIL} please")
    assert redacted.headers["x-rv-disposition"] == "REDACT" and redacted.headers["x-rv-plan-version"] == "b-2"
    deadline = time.time() + 10
    rows: list[dict[str, object]] = []
    while time.time() < deadline and len(rows) < 2:
        rows = _audit_rows(long_rid, "org-b")
        time.sleep(0.2)
    assert sorted(r["phase"] for r in rows) == ["input", "output"]
    assert {r["plan_version"] for r in rows} == {"b-1"}  # input AND output on the pinned version
    per_worker = httpx.get(f"{base}/metrics/all", timeout=10).json()["per_worker"]
    assert all('plan_version_info{org="org-b",version="b-2"}' in w["gauge"] for w in per_worker)
    assert all(w["gauge"]["plan_snapshot_age_seconds"] < 5 for w in per_worker)
    _tool("plan_update.py", "org-b", "b-1", "--rule", "B.pii.in", "--mode", "monitor", "--action", "redact")
    t0 = time.time()
    while time.time() - t0 < 5 and not all(r.headers["x-rv-plan-version"] == "b-1" for r in _sweep(base, KEY_B, 12)):
        pass
    print(json.dumps({"plan_convergence_s": round(t_conv, 3)}))


@pytest.mark.parametrize("scope", ["org", "model"])
def test_killswitch_scope_takes_effect_on_every_worker(base: str, scope: str) -> None:
    key = "org-b" if scope == "org" else MODEL
    flip = _tool("killswitch.py", scope, key, "on")
    try:
        t_eff = None
        while time.time() - float(flip["set_at"]) < 6:
            sweep = _sweep(base, KEY_B)
            if all(r.status_code == 503 and r.json()["error"]["code"] == "kill_switch_engaged" for r in sweep):
                t_eff = time.time() - float(flip["set_at"])
                break
        assert t_eff is not None and t_eff < 5.0, t_eff  # within the declared staleness bound
        if scope == "org":
            assert all(r.status_code == 200 for r in _sweep(base, KEY_A, 8))  # other tenant unaffected
        else:
            assert all(r.status_code == 200 for r in _sweep(base, KEY_B, 8, model="other-model"))
        gauges = httpx.get(f"{base}/metrics/all", timeout=10).json()["per_worker"]
        assert all(w["gauge"]["killswitch_snapshot_age_seconds"] < 5 for w in gauges)
    finally:
        _tool("killswitch.py", scope, key, "off")
    t0 = time.time()
    while time.time() - t0 < 6 and not all(r.status_code == 200 for r in _sweep(base, KEY_B, 12)):
        pass
    assert all(r.status_code == 200 for r in _sweep(base, KEY_B, 12))
    print(json.dumps({"killswitch_scope": scope, "effective_after_s": round(t_eff, 3)}))


def test_quota_lease_counters_bound_admission(base: str) -> None:
    time.sleep(1.2)  # let every worker's 1 s metric dump catch up before the baseline
    before = _counts(base)
    per_worker = httpx.get(f"{base}/metrics/all", timeout=10).json()["per_worker"]
    held_before = sum(w["gauge"].get('lease_held_tokens{org="org-q"}', 0) for w in per_worker)
    _tool("quota.py", "set", "org-q", "5000")
    ok = 0
    client = openai.OpenAI(base_url=f"{base}/v1", api_key=KEY_Q, max_retries=0)
    for _ in range(20):
        try:
            client.chat.completions.create(model=MODEL, max_tokens=1000, messages=[{"role": "user", "content": "hi"}])
            ok += 1
        except openai.RateLimitError:
            break
    time.sleep(1.2)  # worker metric dumps are 1 s apart
    after = _counts(base)
    d = {k: after.get(k, 0) - before.get(k, 0) for k in
         ('quota_admitted_tokens{org="org-q"}', 'lease_granted_tokens{org="org-q"}')}
    admitted, granted = d['quota_admitted_tokens{org="org-q"}'], d['lease_granted_tokens{org="org-q"}']
    # admission is bounded by what was leased now plus what workers already held (the lease
    # model's declared overshoot); the shared budget never hands out more than it has
    assert ok >= 1 and admitted <= granted + held_before and granted <= 5000, (ok, d, held_before)
    assert _tool("quota.py", "get", "org-q")["remaining_tokens"] == 5000 - granted


def test_round_trip_counter_exposed_and_warm_is_zero(base: str) -> None:
    for _ in range(3):
        _probe(base, KEY_A)
    time.sleep(1.2)
    before = _counts(base)
    for _ in range(30):
        _probe(base, KEY_A)
    time.sleep(1.2)
    after = _counts(base)
    zero = after.get('requests_by_round_trips{n="0"}', 0) - before.get('requests_by_round_trips{n="0"}', 0)
    assert zero >= 27, (zero, after)  # warm: identity cached, lease held -> no shared-state trip
    text = httpx.get(f"{base}/metrics", timeout=10).text
    assert 'rv_requests_by_round_trips_total{n="0"}' in text and "rv_shared_state_round_trips_total" in text


LONG = " ".join(f"benign sentence {i} about rivers and maps." for i in range(120))  # ~1k tokens, 2 windows


def test_overload_sheds_immediately_with_retry_after(base: str) -> None:
    """A burst of two-window prompts exceeds the contract-derived guard-work and input-phase
    bounds of the dev stack; the excess is shed at once (503 overloaded + Retry-After +
    x-request-id), admitted requests run every detector."""
    for _ in range(4):  # warm identity cache + lease on every worker: the burst touches no store
        _probe(base, KEY_B)

    async def fire() -> list[httpx.Response]:
        async with httpx.AsyncClient(base_url=base, timeout=120) as c:
            return await asyncio.gather(*(c.post(
                "/v1/chat/completions", headers={"authorization": f"Bearer {KEY_B}", "x-request-id": rid(f"ov{i}")},
                json={"model": MODEL, "max_tokens": 3, "messages": [{"role": "user", "content": LONG}]})
                for i in range(40)))
    time.sleep(1.2)
    before = _counts(base)
    res = asyncio.run(fire())
    shed = [r for r in res if r.status_code == 503]
    admitted = [r for r in res if r.status_code == 200]
    assert shed and admitted and len(shed) + len(admitted) == len(res), [r.status_code for r in res]
    for r in shed:
        err = r.json()["error"]
        assert err["code"] == "overloaded" and err["type"] == "server_overloaded", err
        assert r.headers["retry-after"] and r.headers["retry-after-ms"] and r.headers["x-request-id"]
    for r in admitted:
        assert "sem:E" in r.headers["x-rv-stages"]  # admitted work never skips a detector
    time.sleep(1.2)
    after = _counts(base)
    reasons = {k: after.get(k, 0) - before.get(k, 0) for k in after if k.startswith("shed{")}
    assert sum(reasons.values()) == len(shed), reasons

    async def burst() -> None:
        c = openai.AsyncOpenAI(base_url=f"{base}/v1", api_key=KEY_B, max_retries=0)
        await asyncio.gather(*(c.chat.completions.create(
            model=MODEL, max_tokens=3, messages=[{"role": "user", "content": LONG}]) for _ in range(40)))
    with pytest.raises(openai.InternalServerError) as ei:  # typed for the SDK (5xx)
        asyncio.run(burst())
    assert ei.value.status_code == 503 and ei.value.code == "overloaded" and ei.value.request_id
    gauges = httpx.get(f"{base}/metrics/all", timeout=10).json()["per_worker"]
    assert all("input_queue_cap" in w["gauge"] and "guard_queue_cap_tokens" in w["gauge"] for w in gauges)
    print(json.dumps({"offered": len(res), "shed": len(shed), "admitted": len(admitted), "reasons": reasons}))


HUGE = " ".join(f"benign sentence {i} about rivers and maps." for i in range(330))  # ~2.9k tokens, 7 windows


def test_owner_queue_full_sheds_not_unavailable(base: str) -> None:
    """Owner topology: every worker may admit one request larger than its own guard share into
    an empty queue, so the GPU owner's single queue can receive more than drains within the p99
    target. The owner must refuse that work at once (503 overloaded + Retry-After, counted as
    shed{reason="guard_owner_queue"}) instead of queueing it into deadline expiry (UNAVAILABLE ->
    FAIL_OPEN proceeds without the detector)."""
    d = httpx.get(f"{base}/readyz", timeout=10).json()
    if "(owner)" not in d["guard"]["backend"]:
        pytest.skip(f"guard topology is not owner ({d['guard']['backend']})")
    for _ in range(4):
        _probe(base, KEY_B)
    time.sleep(1.2)
    before = _counts(base)

    async def fire() -> list[httpx.Response]:
        async def one(i: int) -> httpx.Response:
            async with httpx.AsyncClient(base_url=base, timeout=120) as c:  # own connection each
                return await c.post("/v1/chat/completions",
                                    headers={"authorization": f"Bearer {KEY_B}", "x-request-id": rid(f"oq{i}")},
                                    json={"model": MODEL, "max_tokens": 3,
                                          "messages": [{"role": "user", "content": HUGE}]})
        return list(await asyncio.gather(*(one(i) for i in range(12))))

    res = asyncio.run(fire())
    time.sleep(1.2)
    after = _counts(base)
    reasons = {k: after.get(k, 0) - before.get(k, 0) for k in after if k.startswith("shed{")}
    shed = [r for r in res if r.status_code == 503]
    admitted = [r for r in res if r.status_code == 200]
    assert len(shed) + len(admitted) == len(res), [r.status_code for r in res]
    assert reasons.get('shed{reason="guard_owner_queue"}', 0) >= 1, reasons
    assert sum(reasons.values()) == len(shed), reasons
    for r in shed:
        assert r.json()["error"]["code"] == "overloaded" and r.headers["retry-after-ms"], r.text
    for r in admitted:  # admitted work ran the detector; no deadline expiry was turned into a pass
        assert "sem:E" in r.headers["x-rv-stages"], r.headers["x-rv-stages"]
    assert after.get("guard_deadline_expired", 0) == before.get("guard_deadline_expired", 0)
    print(json.dumps({"offered": len(res), "shed": len(shed), "admitted": len(admitted), "reasons": reasons}))
