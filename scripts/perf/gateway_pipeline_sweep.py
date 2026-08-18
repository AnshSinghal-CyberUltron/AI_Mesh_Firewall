#!/usr/bin/env python3
"""Saturation sweep + docker-stats sampler for gateway_pipeline_bench.py.

Phases:
  health  — HTTP ceiling of gunicorn /health (no pipeline)
  sla     — low concurrency full 9-stage chat (customer-facing addon latency)
  sat     — escalating in-flight full 9-stage chat (peak RPS + error taxonomy)

Assumes the live gateway already has GATEWAY_LOADTEST_STUB_LLM=1 and workers
sized to the VM. This script lifts org/key rate limits for the run and restores
them afterwards.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = ROOT / "gateway" / ".venv" / "bin" / "python"
HARNESS = HERE / "gateway_pipeline_bench.py"
OUTDIR = ROOT / "mcp-parallel" / "findings" / "gateway-pipeline-bench-2026-08-14"
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100")
GATEWAY = os.environ.get("GATEWAY", "http://127.0.0.1:8300")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
ORG = os.environ.get("ORG_SLUG", "zeroshield")
REDIS = os.environ.get("REDIS_CTR", "ai_mesh_firewall-redis-1")
CONTAINERS = [
    "ai_mesh_firewall-gateway-1",
    "ai_mesh_firewall-control-1",
    "ai_mesh_firewall-redis-1",
    "ai_mesh_firewall-postgres-1",
    "ai_mesh_firewall-pgbouncer-1",
]


def redis_cli(*args: str) -> str:
    p = subprocess.run(
        ["docker", "exec", REDIS, "redis-cli", "--raw", *args],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return (p.stdout or "").strip()


def docker_stats() -> dict:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    rows = {}
    for line in out.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, cpu, mem, pids = parts[0], parts[1], parts[2], parts[3]
        if name not in CONTAINERS:
            continue
        try:
            cpu_f = float(cpu.strip().rstrip("%"))
        except ValueError:
            cpu_f = 0.0
        rows[name] = {"cpu_pct": cpu_f, "cores": round(cpu_f / 100, 2), "mem": mem, "pids": pids}
    return rows


def mint_key() -> str:
    with httpx.Client(timeout=60) as c:
        tok = c.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASSWORD})
        tok.raise_for_status()
        jwt = tok.json().get("access") or tok.json().get("access_token")
        r = c.post(
            f"{CONTROL}/api/gateways/simulator-default/",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        r.raise_for_status()
        key = r.json().get("key")
        if not key:
            raise RuntimeError(f"no simulator key: {r.text[:400]}")
        return key


def lift_rate_limits(api_key: str) -> dict:
    """Disable org rate_limit_enabled + zero per-key TPM. Snapshot for restore."""
    cfg_key = f"firewall:config:{ORG}"
    raw = redis_cli("GET", cfg_key)
    existed = bool(raw) and raw != "(nil)"
    if not existed:
        raw = redis_cli("GET", "firewall:config:default")
    snap = {
        "config_raw": raw if existed else None,
        "config_existed": existed,
        "auth_raw": None,
        "auth_redis_key": None,
    }
    if raw and raw != "(nil)":
        data = json.loads(raw)
        data["org_slug"] = ORG
        data["rate_limit_enabled"] = False
        data["burst_limit"] = 1000000
        data["requests_per_minute"] = 10000000
        data["org_tpm_limit"] = 0
        redis_cli("SET", cfg_key, json.dumps(data))
        redis_cli("PUBLISH", "config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    h = hashlib.sha256(api_key.encode()).hexdigest()
    auth_k = f"auth:apikey:{h}"
    araw = redis_cli("GET", auth_k)
    snap["auth_redis_key"] = auth_k
    snap["auth_raw"] = araw if araw and araw != "(nil)" else None
    if snap["auth_raw"]:
        payload = json.loads(snap["auth_raw"])
        payload["rate_limit_tpm"] = 0
        payload["rpm_limit"] = 0
        redis_cli("SET", auth_k, json.dumps(payload))
    return snap


def restore_rate_limits(snap: dict) -> None:
    cfg_key = f"firewall:config:{ORG}"
    if snap.get("config_existed") and snap.get("config_raw"):
        redis_cli("SET", cfg_key, snap["config_raw"])
        redis_cli("PUBLISH", "config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    elif not snap.get("config_existed"):
        redis_cli("DEL", cfg_key)
        redis_cli("PUBLISH", "config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    if snap.get("auth_redis_key") and snap.get("auth_raw"):
        redis_cli("SET", snap["auth_redis_key"], snap["auth_raw"])


def run_phase(label: str, mode: str, workers: int, conn: int, duration: float, env_extra: dict, target_calls: int = 0) -> dict:
    out = OUTDIR / f"{label}.json"
    env = {
        **os.environ,
        "GATEWAY": GATEWAY,
        "MODE": mode,
        "WORKERS": str(workers),
        "CONN": str(conn),
        "DURATION_S": str(duration),
        "OUT": str(out),
        **env_extra,
    }
    if target_calls:
        env["TARGET_CALLS"] = str(target_calls)
        env["DURATION_S"] = str(max(duration, target_calls * 30))
    t0 = time.time()
    stats_before = docker_stats()
    p = subprocess.run(
        [str(PY), str(HARNESS)],
        env=env, capture_output=True, text=True,
        timeout=int(float(env["DURATION_S"])) + 180,
    )
    stats_after = docker_stats()
    try:
        r = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r = {
            "error": "no report",
            "returncode": p.returncode,
            "stdout": (p.stdout or "")[-800:],
            "stderr": (p.stderr or "")[-800:],
        }
    r["_label"] = label
    r["_inflight"] = workers * conn
    r["_wall_orchestrated"] = round(time.time() - t0, 2)
    r["_docker_before"] = stats_before
    r["_docker_after"] = stats_after
    out.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    print(f"== {label} inflight={workers * conn} rps={r.get('rps')} ok_rps={r.get('ok_rps')} "
          f"err={r.get('errors')} addon_p50={((r.get('addon_latency_ms') or {}).get('p50'))}",
          flush=True)
    return r


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("API_KEY") or mint_key()
    snap = lift_rate_limits(key)
    (OUTDIR / "rate_limit_snapshot.json").write_text(json.dumps({
        "config_len": len(snap.get("config_raw") or ""),
        "auth_key": snap.get("auth_redis_key"),
        "lifted": True,
    }, indent=2) + "\n")
    extra = {
        "API_KEY": key,
        "MAX_TOKENS": "16",
        "PROMPT": "Reply with the single word ping.",
        "MODEL": os.environ.get("MODEL", "gpt-5.2"),
        "TIMEOUT_S": os.environ.get("TIMEOUT_S", "90"),
    }
    results = []
    try:
        # Infra ceiling: /health. Keep inflight modest — ab -c800 hung this host.
        # Honest HTTP peak is apachebench ~9182 RPS (see ab_health_c400.txt).
        for inflight, dur in ((256, 6), (1024, 6)):
            w = 16
            c = max(1, inflight // w)
            results.append(run_phase(f"health_if{inflight}", "health", w, c, dur, extra))
        # Customer-facing addon at light load (full 9-stage, stub LLM).
        results.append(run_phase("chat_sla_c1", "chat", 1, 1, 90, extra, target_calls=16))
        results.append(run_phase("chat_sla_c8", "chat", 2, 4, 25, extra))
        # Saturation of the full pipeline (live Tier-2 Bedrock × 3 per request).
        for inflight, dur in ((8, 15), (16, 15), (32, 15), (64, 15), (128, 12), (256, 12)):
            w = min(16, inflight)
            c = max(1, inflight // w)
            results.append(run_phase(f"chat_sat_if{inflight}", "chat", w, c, dur, extra))
    finally:
        restore_rate_limits(snap)

    health = [r for r in results if str(r.get("mode")) == "health" and "rps" in r]
    chat_sat = [r for r in results if str(r.get("_label", "")).startswith("chat_sat") and "rps" in r]
    sla = [r for r in results if str(r.get("_label", "")).startswith("chat_sla") and "rps" in r]
    peak_health = max(health, key=lambda x: x.get("ok_rps") or 0) if health else {}
    peak_chat = max(chat_sat, key=lambda x: x.get("ok_rps") or 0) if chat_sat else {}
    sla_c1 = next((r for r in sla if r.get("_label") == "chat_sla_c1"), {})
    verdict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vm": {"nproc": 16, "ram_gb": 60, "note": "this docker host"},
        "honesty_100k_rps": {
            "target": 100000,
            "health_peak_ok_rps": peak_health.get("ok_rps"),
            "full_pipeline_peak_ok_rps": peak_chat.get("ok_rps"),
            "why_not_100k_chat": (
                "Full 9-stage includes Tier-2 Bedrock input scan + routing adjudicator "
                "+ output guard. Little's Law: RPS ≈ in-flight / latency. A ~200–2000ms "
                "Bedrock scan cannot yield 100k RPS on 16 cores. Health RPS is the HTTP "
                "ceiling of this gunicorn; chat RPS is the product ceiling."
            ),
        },
        "customer_addon_ms": {
            "definition": "pipeline_trace.total_latency_ms − model_output_ms (BYOK inference excluded; LLM stubbed)",
            "concurrency_1": sla_c1.get("addon_latency_ms"),
            "concurrency_8": next((r.get("addon_latency_ms") for r in sla if r.get("_label") == "chat_sla_c8"), None),
            "stages_c1": sla_c1.get("stage_latency_ms"),
        },
        "peak_health": {"label": peak_health.get("_label"), "ok_rps": peak_health.get("ok_rps"),
                        "inflight": peak_health.get("_inflight"), "p50_ms": (peak_health.get("client_latency_ms") or {}).get("p50")},
        "peak_full_pipeline": {"label": peak_chat.get("_label"), "ok_rps": peak_chat.get("ok_rps"),
                               "inflight": peak_chat.get("_inflight"),
                               "error_rate": peak_chat.get("error_rate"),
                               "errors_by_signature": peak_chat.get("errors_by_signature")},
        "saturation_table": [
            {"label": r.get("_label"), "inflight": r.get("_inflight"), "rps": r.get("rps"),
             "ok_rps": r.get("ok_rps"), "error_rate": r.get("error_rate"),
             "addon_p50": (r.get("addon_latency_ms") or {}).get("p50"),
             "wall_p50": (r.get("client_latency_ms") or {}).get("p50"),
             "codes": r.get("codes"),
             "top_errors": dict(list((r.get("errors_by_signature") or {}).items())[:5]),
             "gw_cpu": ((r.get("_docker_after") or {}).get("ai_mesh_firewall-gateway-1") or {}).get("cpu_pct"),
             }
            for r in results if "rps" in r
        ],
        "phases": [r.get("_label") for r in results],
    }
    (OUTDIR / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
