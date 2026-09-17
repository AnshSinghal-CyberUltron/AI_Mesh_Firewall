#!/usr/bin/env python3
"""T01 extras: zeroshield Redis PII key, OpenRouter BYOK if key file exists, honest perf.

Never prints secrets. Key file: /tmp/t01-openrouter.key (0600, optional).
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

KEYS = Path("/tmp/v3a02.keys.json")
EVIDENCE = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/plans/evidence/2026-09-17-t01")
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
REDIS = "ai_mesh_firewall-redis-1"
OR_KEY = Path("/tmp/t01-openrouter.key")


def _http(method, url, *, token=None, api_key=None, body=None, timeout=60):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode() or "null") if raw else None
            return resp.status, parsed, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode() or "null")
        except Exception:
            parsed = {"_raw": raw[:400].decode("utf-8", "replace")}
        return exc.code, parsed, dict(exc.headers)


def pipeline_trace(body) -> dict:
    if not isinstance(body, dict):
        return {}
    if isinstance(body.get("pipeline_trace"), dict):
        return body["pipeline_trace"]
    meta = body.get("metadata") or {}
    if isinstance(meta, dict) and isinstance(meta.get("pipeline_trace"), dict):
        return meta["pipeline_trace"]
    zs = body.get("zeroshield") or {}
    if isinstance(zs, dict) and isinstance(zs.get("pipeline_trace"), dict):
        return zs["pipeline_trace"]
    return {}


def addon_ms(trace: dict) -> float | None:
    if not trace:
        return None
    total = trace.get("total_latency_ms")
    metrics = trace.get("metrics") or {}
    model = 0.0
    try:
        model = float(metrics.get("model_output_ms") or 0.0)
    except (TypeError, ValueError):
        model = 0.0
    stages = {s.get("name"): s for s in (trace.get("stages") or []) if isinstance(s, dict)}
    if "model_output" in stages:
        try:
            model = float((stages["model_output"] or {}).get("latency_ms") or model or 0.0)
        except (TypeError, ValueError):
            pass
    try:
        total_f = float(total or 0.0)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, total_f - model), 1)


def redis_get(key: str) -> str:
    return subprocess.check_output(
        ["docker", "exec", REDIS, "redis-cli", "GET", key], text=True
    ).strip()


def main() -> None:
    keys = json.loads(KEYS.read_text())
    email = keys["frontend"]["email"]
    password = keys["frontend"]["password"]
    block_key = keys["orgs"]["v3a02-block"]["raw"]
    out = {"task": "T01", "extras": {}}

    st, tok, _ = _http(
        "POST", f"{CONTROL}/api/auth/token/", body={"email": email, "password": password}
    )
    if st != 200:
        raise SystemExit(f"synth login failed {st}")
    token = tok["access"]

    st, models, _ = _http("GET", f"{CONTROL}/api/firewall/models/", token=token)
    model_rows = models if isinstance(models, list) else []
    out["extras"]["models_get_status"] = st
    out["extras"]["models"] = [
        {
            "id": m.get("id"),
            "provider": m.get("provider"),
            "model_name": m.get("model_name"),
            "model_id": m.get("model_id"),
            "api_base_host": (m.get("api_base") or "").split("/")[2] if m.get("api_base") else "",
            "api_key_set": m.get("api_key_set"),
            "is_active": m.get("is_active"),
        }
        for m in model_rows
        if isinstance(m, dict)
    ]

    raw_key = ""
    if OR_KEY.exists() and OR_KEY.stat().st_size > 40:
        raw_key = OR_KEY.read_text().strip()
    out["extras"]["openrouter_key_file_present"] = bool(raw_key)
    out["extras"]["openrouter_key_len"] = len(raw_key) if raw_key else 0

    target = None
    for m in model_rows:
        if str(m.get("model_name") or "") in {"gpt-4o-mini", "openai/gpt-4o-mini"}:
            target = m
            break
    if target is None and model_rows:
        target = model_rows[0]

    inference = {"attempted": False}
    if raw_key and target and target.get("id"):
        body = {
            "provider": "custom",
            "api_base": "https://openrouter.ai/api/v1",
            "model_id": "openai/gpt-4o-mini",
            "model_name": target.get("model_name") or "gpt-4o-mini",
            "api_key": raw_key,
            "is_active": True,
            "data_sensitivity_level": target.get("data_sensitivity_level") or "public",
            "cost_per_1k_input_tokens": str(
                target.get("cost_per_1k_input_tokens") if target.get("cost_per_1k_input_tokens") is not None else "0.001"
            ),
            "latency_sla_ms": target.get("latency_sla_ms") if target.get("latency_sla_ms") is not None else 8000,
            "routing_priority": target.get("routing_priority") if target.get("routing_priority") is not None else 1,
            "risk_score": target.get("risk_score") if target.get("risk_score") is not None else 0.2,
        }
        pst, pbody, ph = _http(
            "PATCH",
            f"{CONTROL}/api/firewall/models/{target['id']}/",
            token=token,
            body=body,
        )
        inference["attempted"] = True
        inference["patch_status"] = pst
        inference["patch_api_key_set"] = (pbody or {}).get("api_key_set") if isinstance(pbody, dict) else None
        inference["patch_provider"] = (pbody or {}).get("provider") if isinstance(pbody, dict) else None
        inference["patch_x_request_id"] = ph.get("x-request-id") or ph.get("X-Request-Id")
        time.sleep(2.5)
        http_i, chat_i, hdr_i = _http(
            "POST",
            f"{GATEWAY}/v1/chat/completions",
            api_key=block_key,
            body={
                "model": target.get("model_name") or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Reply with the single word pong."}],
                "max_tokens": 8,
                "stream": False,
            },
            timeout=90,
        )
        inference["http"] = http_i
        inference["x_request_id"] = hdr_i.get("x-request-id") or hdr_i.get("X-Request-Id")
        inference["has_choices"] = bool(isinstance(chat_i, dict) and chat_i.get("choices"))
        inference["error_code"] = None
        if isinstance(chat_i, dict):
            err = chat_i.get("error")
            if isinstance(err, dict):
                inference["error_code"] = err.get("code") or err.get("type")
            elif isinstance(err, str):
                inference["error_code"] = err[:80]
        inference["pass"] = http_i == 200 and inference["has_choices"]
    else:
        inference["pass"] = False
        inference["reason"] = "no_openrouter_key_file_or_no_model"
    out["extras"]["inference"] = inference

    # zeroshield Redis pii_detection_enabled backfill via JWT PUT of current value
    zs = {"attempted": False}
    try:
        stz, tokz, _ = _http(
            "POST",
            f"{CONTROL}/api/auth/token/",
            body={"email": "admin@zeroshield.io", "password": "Adm1n!Pass#2024"},
        )
        zs["login"] = stz
        if stz == 200:
            ztoken = tokz["access"]
            stg, cfg, _ = _http("GET", f"{CONTROL}/api/firewall/config/", token=ztoken)
            zs["get_status"] = stg
            current = (cfg or {}).get("pii_detection_enabled")
            zs["db_pii_detection_enabled"] = current
            before = redis_get("firewall:config:zeroshield")
            before_j = json.loads(before) if before and before != "(nil)" else {}
            zs["redis_had_key_before"] = "pii_detection_enabled" in before_j
            if stg == 200:
                zs["attempted"] = True
                stp, _, _ = _http(
                    "PUT",
                    f"{CONTROL}/api/firewall/config/",
                    token=ztoken,
                    body={"pii_detection_enabled": True if current is None else current},
                )
                zs["put_status"] = stp
                time.sleep(1.0)
                after = redis_get("firewall:config:zeroshield")
                after_j = json.loads(after) if after and after != "(nil)" else {}
                zs["redis_has_key_after"] = "pii_detection_enabled" in after_j
                zs["redis_value"] = after_j.get("pii_detection_enabled")
                zs["pass"] = stp == 200 and zs["redis_has_key_after"]
    except Exception as exc:
        zs["error"] = type(exc).__name__
    out["extras"]["zeroshield_pii_redis"] = zs

    # Honest addon p99 + openloop RPS (scan-only, no OpenRouter blast)
    addons = []
    errors = 0

    def one_scan():
        return _http(
            "POST",
            f"{GATEWAY}/v1/chat/completions",
            api_key=block_key,
            body={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "ping t01 addon"}],
                "max_tokens": 0,
                "stream": False,
            },
            timeout=60,
        )

    for _ in range(40):
        http, body, _hdr = one_scan()
        if http >= 500:
            errors += 1
            continue
        a = addon_ms(pipeline_trace(body))
        if a is not None:
            addons.append(a)

    addons_sorted = sorted(addons)
    p99 = None
    if addons_sorted:
        idx = min(len(addons_sorted) - 1, int(round(0.99 * (len(addons_sorted) - 1))))
        p99 = addons_sorted[idx]
    perf = {
        "scan_only_n": len(addons_sorted),
        "errors_5xx": errors,
        "addon_ms_p50": statistics.median(addons_sorted) if addons_sorted else None,
        "addon_ms_p99": p99,
        "addon_ms_max": max(addons_sorted) if addons_sorted else None,
        "target_addon_p99_ms": 20,
        "addon_p99_meets_20ms": bool(p99 is not None and p99 < 20),
    }

    # 8s openloop at 32 in-flight
    inflight = 32
    duration_s = 8.0
    ok = 0
    fail = 0
    lock = threading.Lock()
    t0 = time.perf_counter()

    def worker():
        nonlocal ok, fail
        while time.perf_counter() - t0 < duration_s:
            http, _body, _h = one_scan()
            with lock:
                if 200 <= http < 500:
                    ok += 1
                else:
                    fail += 1

    with ThreadPoolExecutor(max_workers=inflight) as pool:
        futs = [pool.submit(worker) for _ in range(inflight)]
        for f in as_completed(futs):
            f.result()
    elapsed = time.perf_counter() - t0
    rps = ok / elapsed if elapsed else 0.0
    perf["openloop"] = {
        "inflight": inflight,
        "duration_s": round(elapsed, 2),
        "ok": ok,
        "fail": fail,
        "rps": round(rps, 2),
        "floor_1064": 1064,
        "meets_1064": rps >= 1064,
        "note": "4-node L4 fleet floor; this VM scan-only openloop. Do not claim 1064 unless measured.",
    }
    out["extras"]["perf"] = perf

    dest = EVIDENCE / "t01_extras.json"
    dest.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
