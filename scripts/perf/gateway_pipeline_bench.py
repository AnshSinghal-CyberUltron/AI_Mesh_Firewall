#!/usr/bin/env python3
"""Closed-loop gateway load driver: /health or full-pipeline /v1/chat/completions.

Multiprocess × asyncio, no per-round barrier. Records RPS, latency percentiles,
per-stage pipeline_trace, gateway addon (total − model_output), and error taxonomy.

Usage:
  GATEWAY=http://127.0.0.1:8300 API_KEY=zs-... \\
    WORKERS=8 CONN=16 DURATION_S=20 MODE=chat \\
    gateway/.venv/bin/python scripts/perf/gateway_pipeline_bench.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import Counter
from multiprocessing import Process, Queue

import httpx

GATEWAY = os.environ.get("GATEWAY", "http://127.0.0.1:8300").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
MODE = os.environ.get("MODE", "chat")  # health | chat
WORKERS = max(1, int(os.environ.get("WORKERS", "8")))
CONN = max(1, int(os.environ.get("CONN", "8")))
DURATION_S = float(os.environ.get("DURATION_S", "15"))
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "120"))
CONNECT_TIMEOUT_S = float(os.environ.get("CONNECT_TIMEOUT_S", "10"))
RAMP_S = float(os.environ.get("RAMP_S", "0"))
SSL_VERIFY = os.environ.get("SSL_VERIFY", "1").strip().lower() not in ("0", "false", "no", "off")
HTTP_HOST = os.environ.get("HTTP_HOST", "").strip()
TARGET_CALLS = int(os.environ.get("TARGET_CALLS", "0"))  # 0 = time-based
OUT = os.environ.get("OUT", "")
MODEL = os.environ.get("MODEL", "gpt-4o-mini")
PROMPT = os.environ.get("PROMPT", "Summarize the weather in one short sentence.")
UNIQUE_PROMPT = os.environ.get("UNIQUE_PROMPT", "0").strip().lower() in ("1", "true", "yes", "on")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16"))
ENABLE_ROUTING = os.environ.get("ENABLE_ROUTING", "1").strip().lower() not in ("0", "false", "no", "off")
STAGE_NAMES = (
    "auth", "rate_limit", "policy", "input_scan", "kill_switch",
    "model_routing", "model_input", "model_output", "output_guardrail",
)


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1))))
    return round(sorted_vals[idx], 3)


def _mean(vals):
    return round(sum(vals) / len(vals), 3) if vals else None


def addon_from_trace(trace: dict | None) -> tuple[float | None, dict]:
    """Gateway addon = wall pipeline total minus BYOK model_output."""
    if not isinstance(trace, dict):
        return None, {}
    stages = {}
    for s in (trace.get("stages") or []):
        if not isinstance(s, dict):
            continue
        key = s.get("stage") or s.get("name")
        if key:
            stages[key] = s
    metrics = trace.get("metrics") if isinstance(trace.get("metrics"), dict) else {}
    by_stage = {}
    for name in STAGE_NAMES:
        ms = None
        st = stages.get(name) or {}
        if st.get("latency_ms") is not None:
            try:
                ms = float(st["latency_ms"])
            except (TypeError, ValueError):
                ms = None
        if ms is None and metrics.get(f"{name}_ms") is not None:
            try:
                ms = float(metrics[f"{name}_ms"])
            except (TypeError, ValueError):
                ms = None
        if ms is not None:
            by_stage[name] = round(ms, 3)
    total = trace.get("total_latency_ms")
    try:
        total_f = float(total) if total is not None else None
    except (TypeError, ValueError):
        total_f = None
    if total_f is None and by_stage:
        total_f = round(sum(by_stage.values()), 3)
    model_out = by_stage.get("model_output", 0.0) or 0.0
    addon = round(max(0.0, (total_f or 0.0) - model_out), 3) if total_f is not None else None
    by_stage["total"] = total_f
    by_stage["addon"] = addon
    return addon, by_stage

STUB_IDS = frozenset({"chatcmpl-loadtest-stub"})
REQUIRED_LIVE_STAGES = ("input_scan", "output_guardrail")
_STUB_ENV_TRUTHY = frozenset({"1", "true", "yes", "on"})


def extract_completion_id(body: object) -> str | None:
    if not isinstance(body, dict):
        return None
    cid = body.get("id")
    if isinstance(cid, str) and cid.strip():
        return cid.strip()[:80]
    return None


def _stage_p50(stage_latency_ms: dict | None, name: str) -> float | None:
    rec = (stage_latency_ms or {}).get(name)
    if not isinstance(rec, dict):
        return None
    try:
        return float(rec["p50"]) if rec.get("p50") is not None else None
    except (TypeError, ValueError):
        return None


def compute_full_nine_stages(*, mode: str, stage_latency_ms: dict | None = None) -> bool:
    """True only when chat actually ran live input_scan and output_guardrail.

    MODE==chat is not enough: scans-off and /health-shaped chat both fail.
    """
    if mode != "chat":
        return False
    for name in REQUIRED_LIVE_STAGES:
        p50 = _stage_p50(stage_latency_ms, name)
        if p50 is None or p50 <= 0:
            return False
    return True


def _stub_llm_flag(stub_llm_env: str, completion_ids: dict | None) -> bool:
    env_on = (stub_llm_env or "").strip().lower() in _STUB_ENV_TRUTHY
    ids_hit = any(cid in STUB_IDS for cid in (completion_ids or {}))
    return env_on or ids_hit


def compute_capacity_fail_reasons(
    *,
    mode: str,
    full_nine_stages: bool,
    stub_llm: bool,
    unique_prompt: bool,
) -> list[str]:
    reasons: list[str] = []
    if mode != "chat":
        reasons.append("mode_not_chat")
    if not full_nine_stages:
        reasons.append("not_full_nine_stages")
    if stub_llm:
        reasons.append("stub_llm")
    if not unique_prompt:
        reasons.append("not_unique_prompt")
    return reasons


def build_honesty_report(
    *,
    mode: str,
    stage_latency_ms: dict | None,
    unique_prompt: bool,
    stub_llm_env: str,
    completion_ids: dict | None = None,
) -> dict:
    stub_llm = _stub_llm_flag(stub_llm_env, completion_ids)
    full = compute_full_nine_stages(mode=mode, stage_latency_ms=stage_latency_ms)
    reasons = compute_capacity_fail_reasons(
        mode=mode,
        full_nine_stages=full,
        stub_llm=stub_llm,
        unique_prompt=bool(unique_prompt),
    )
    eligible = not reasons
    return {
        "addon_definition": (
            "pipeline_trace.total_latency_ms - model_output_ms (BYOK inference excluded)"
        ),
        "stub_llm": stub_llm,
        "stub_llm_env": stub_llm_env,
        "full_nine_stages": full,
        "unique_prompt": bool(unique_prompt),
        "capacity_eligible": eligible,
        "capacity_predicate": "pass" if eligible else "fail",
        "capacity_fail_reasons": reasons,
        "completion_ids": dict(completion_ids or {}),
    }


def _err_sig(status: int, body: dict | str, exc: str) -> str:
    if exc:
        return f"EXC:{exc}"
    code = ""
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "")
            msg = str(err.get("message") or "")[:160]
        else:
            code = str(body.get("code") or "")
            msg = str(body.get("message") or err or "")[:160]
    elif isinstance(body, str) and body:
        msg = body[:160]
    return f"HTTP {status} {code} {msg}".strip()


def _chat_prompt() -> str:
    if UNIQUE_PROMPT:
        return f"{PROMPT} Marker TOK-{time.time_ns():x}."
    return PROMPT


async def _one_chat(client: httpx.AsyncClient) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{GATEWAY}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": _chat_prompt()}],
                "max_tokens": MAX_TOKENS,
                "stream": False,
                **({} if ENABLE_ROUTING else {"enable_routing": False}),
            },
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        try:
            body = r.json()
        except Exception:
            body = {"_raw": (r.text or "")[:300]}
        trace = None
        if isinstance(body, dict):
            trace = body.get("pipeline_trace")
            if trace is None and isinstance(body.get("zeroshield"), dict):
                trace = body["zeroshield"].get("pipeline_trace")
        addon, stages = addon_from_trace(trace if isinstance(trace, dict) else None)
        ok = 200 <= r.status_code < 300
        return {
            "ok": ok,
            "status": r.status_code,
            "wall_ms": wall_ms,
            "addon_ms": addon,
            "stages": stages,
            "completion_id": extract_completion_id(body) if isinstance(body, dict) else None,
            "sig": None if ok else _err_sig(r.status_code, body, ""),
            "scan_only": bool(
                isinstance(body, dict)
                and isinstance(body.get("zeroshield"), dict)
                and body["zeroshield"].get("scan_only")
            ),
        }
    except Exception as exc:  # noqa: BLE001
        wall_ms = (time.perf_counter() - t0) * 1000
        name = type(exc).__name__
        return {
            "ok": False,
            "status": 0,
            "wall_ms": wall_ms,
            "addon_ms": None,
            "stages": {},
            "completion_id": None,
            "sig": _err_sig(0, {}, name),
            "scan_only": False,
        }


async def _one_health(client: httpx.AsyncClient) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.get(f"{GATEWAY}/health")
        wall_ms = (time.perf_counter() - t0) * 1000
        ok = r.status_code == 200
        return {
            "ok": ok,
            "status": r.status_code,
            "wall_ms": wall_ms,
            "addon_ms": None,
            "stages": {},
            "completion_id": None,
            "sig": None if ok else _err_sig(r.status_code, r.text[:160], ""),
            "scan_only": False,
        }
    except Exception as exc:  # noqa: BLE001
        wall_ms = (time.perf_counter() - t0) * 1000
        return {
            "ok": False, "status": 0, "wall_ms": wall_ms, "addon_ms": None,
            "stages": {}, "completion_id": None,
            "sig": _err_sig(0, {}, type(exc).__name__), "scan_only": False,
        }


async def _worker_loop(quota: list, stop_at: float, results: list):
    limits = httpx.Limits(max_connections=CONN, max_keepalive_connections=CONN)
    timeout = httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    fire = _one_chat if MODE == "chat" else _one_health
    client_headers = {"Host": HTTP_HOST} if HTTP_HOST else None
    async with httpx.AsyncClient(
        http2=False,
        limits=limits,
        timeout=timeout,
        verify=SSL_VERIFY,
        headers=client_headers,
    ) as client:
        sem = asyncio.Semaphore(CONN)

        async def _run_one():
            async with sem:
                return await fire(client)

        inflight: set[asyncio.Task] = set()
        ramp_t0 = time.monotonic()

        def inflight_cap() -> int:
            if RAMP_S <= 0:
                return CONN
            elapsed = time.monotonic() - ramp_t0
            if elapsed >= RAMP_S:
                return CONN
            return max(1, int(CONN * (elapsed / RAMP_S)))

        while True:
            if TARGET_CALLS and quota[0] <= 0 and not inflight:
                break
            if not TARGET_CALLS and time.monotonic() >= stop_at and not inflight:
                break
            can_launch = (not TARGET_CALLS or quota[0] > 0) and (
                TARGET_CALLS or time.monotonic() < stop_at
            )
            while can_launch and len(inflight) < inflight_cap():
                if TARGET_CALLS:
                    quota[0] -= 1
                inflight.add(asyncio.create_task(_run_one()))
                can_launch = (not TARGET_CALLS or quota[0] > 0) and (
                    TARGET_CALLS or time.monotonic() < stop_at
                )
            if not inflight:
                break
            done, inflight = await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                results.append(t.result())


def _child(q: Queue, quota: int, stop_at: float):
    recs: list = []
    asyncio.run(_worker_loop([quota], stop_at, recs))
    walls = [r["wall_ms"] for r in recs]
    addons = [r["addon_ms"] for r in recs if r.get("addon_ms") is not None]
    stage_sums: dict[str, list] = {n: [] for n in STAGE_NAMES}
    stage_sums["total"] = []
    stage_sums["addon"] = []
    for r in recs:
        st = r.get("stages") or {}
        for k, v in st.items():
            if v is None:
                continue
            stage_sums.setdefault(k, []).append(v)
    codes = Counter(r["status"] for r in recs)
    sigs = Counter(r["sig"] for r in recs if r.get("sig"))
    completion_ids = Counter(r["completion_id"] for r in recs if r.get("completion_id"))
    q.put({
        "n": len(recs),
        "ok": sum(1 for r in recs if r["ok"]),
        "scan_only": sum(1 for r in recs if r.get("scan_only")),
        "codes": dict(codes),
        "sigs": dict(sigs),
        "completion_ids": dict(completion_ids),
        "walls": walls[-8000:],
        "addons": addons[-8000:],
        "stage_samples": {k: v[-2000:] for k, v in stage_sums.items() if v},
    })


def _merge_pct(samples):
    samples = sorted(samples)
    return {
        "n": len(samples),
        "mean": _mean(samples),
        "p50": _pct(samples, 0.50),
        "p90": _pct(samples, 0.90),
        "p95": _pct(samples, 0.95),
        "p99": _pct(samples, 0.99),
        "max": round(samples[-1], 3) if samples else None,
    }


def run() -> dict:
    if MODE == "chat" and not API_KEY:
        raise SystemExit("API_KEY required for MODE=chat")
    stop_at = time.monotonic() + DURATION_S
    per = TARGET_CALLS // WORKERS if TARGET_CALLS else 10**12
    q: Queue = Queue()
    procs = [
        Process(target=_child, args=(q, per, stop_at))
        for _ in range(WORKERS)
    ]
    t0 = time.perf_counter()
    for p in procs:
        p.start()
    parts = [q.get() for _ in procs]
    for p in procs:
        p.join()
    elapsed = time.perf_counter() - t0
    n = sum(p["n"] for p in parts)
    ok = sum(p["ok"] for p in parts)
    codes: Counter = Counter()
    sigs: Counter = Counter()
    completion_ids: Counter = Counter()
    walls: list = []
    addons: list = []
    stages: dict[str, list] = {}
    for p in parts:
        codes.update(p["codes"])
        sigs.update(p["sigs"])
        completion_ids.update(p.get("completion_ids") or {})
        walls.extend(p["walls"])
        addons.extend(p["addons"])
        for k, v in (p.get("stage_samples") or {}).items():
            stages.setdefault(k, []).extend(v)
    if len(walls) > 40000:
        walls = random.sample(walls, 40000)
    if len(addons) > 40000:
        addons = random.sample(addons, 40000)
    stage_latency_ms = {k: _merge_pct(v) for k, v in stages.items() if v}
    report = {
        "mode": MODE,
        "gateway": GATEWAY,
        "workers": WORKERS,
        "conn": CONN,
        "inflight": WORKERS * CONN,
        "ramp_s": RAMP_S,
        "duration_s": round(elapsed, 3),
        "requests": n,
        "ok": ok,
        "errors": n - ok,
        "error_rate": round((n - ok) / n, 4) if n else None,
        "rps": round(n / elapsed, 2) if elapsed else 0,
        "ok_rps": round(ok / elapsed, 2) if elapsed else 0,
        "scan_only": sum(p["scan_only"] for p in parts),
        "codes": dict(codes),
        "errors_by_signature": dict(sigs.most_common(40)),
        "client_latency_ms": _merge_pct(walls),
        "addon_latency_ms": _merge_pct(addons),
        "stage_latency_ms": stage_latency_ms,
        "honesty": build_honesty_report(
            mode=MODE,
            stage_latency_ms=stage_latency_ms,
            unique_prompt=UNIQUE_PROMPT,
            stub_llm_env=os.environ.get("GATEWAY_LOADTEST_STUB_LLM", ""),
            completion_ids=dict(completion_ids),
        ),
    }
    if OUT:
        os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    run()
