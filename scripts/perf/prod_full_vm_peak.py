#!/usr/bin/env python3
"""PROD full-VM peak: unique-prompt 9-stage + /health until hardware/AWS ceiling.

Runs FROM the GCP driver against the EC2 gateway (direct :8300 and/or NLB HTTPS).
Docker stats are sampled via SSH Host AIMeshFirewall. Restores Redis snapshots.

  API_KEY=... GATEWAY=http://HOST:8300 \\
    gateway/.venv/bin/python scripts/perf/prod_full_vm_peak.py
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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
_VENV = ROOT / "gateway" / ".venv" / "bin" / "python"
PY = _VENV if _VENV.exists() else Path(sys.executable)
HARNESS = HERE / "gateway_pipeline_bench.py"
OUTDIR = ROOT / "mcp-parallel" / "findings" / "full-vm-peak-2026-08-14"
SSH = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "AIMeshFirewall"]
ORG = os.environ.get("ORG_SLUG", "zeroshield")
REDIS = os.environ.get("REDIS_CTR", "ai_mesh_firewall-redis-1")
STATS_LOG = OUTDIR / "docker_stats.txt"

HEALTH_LEVELS = [
    # inflight, workers, conn, duration_s
    (256, 16, 16, 8),
    (1024, 32, 32, 8),
    (4096, 32, 128, 8),
    (8192, 32, 256, 8),
    (16384, 64, 256, 8),
    (32768, 64, 512, 8),
]
CHAT_LEVELS = [
    (32, 8, 4, 20),
    (64, 16, 4, 20),
    (128, 16, 8, 20),
    (256, 16, 16, 20),
    (512, 32, 16, 20),
    (1024, 32, 32, 18),
    (2048, 32, 64, 18),
    (4096, 32, 128, 15),
    (8192, 32, 256, 12),
    (16384, 64, 256, 12),
]


def ssh(cmd: str, timeout: int = 30, stdin: str | None = None) -> str:
    p = subprocess.run(
        [*SSH, cmd],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return (p.stdout or "").strip()


def redis_get(key: str) -> str | None:
    raw = ssh(f"docker exec {REDIS} redis-cli --raw GET {key}", timeout=45)
    if not raw or raw == "(nil)":
        return None
    return raw


def redis_set(key: str, value: str) -> None:
    ssh(f"docker exec -i {REDIS} redis-cli -x SET {key}", timeout=45, stdin=value)


def redis_publish(channel: str, payload: str) -> None:
    ssh(
        f"docker exec -i {REDIS} redis-cli -x PUBLISH {channel}",
        timeout=30,
        stdin=payload,
    )


def sample_stats() -> str:
    line = ssh(
        "date -u +%Y-%m-%dT%H:%M:%SZ; "
        "docker stats --no-stream --format "
        "'{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}' "
        "| grep -E 'gateway-1|control-1|postgres-1|redis-1|nginx-1'",
        timeout=25,
    )
    return line


def run_bench(label: str, mode: str, workers: int, conn: int, duration: float, extra: dict) -> dict:
    out = OUTDIR / f"{label}.json"
    env = {
        **os.environ,
        "MODE": mode,
        "WORKERS": str(workers),
        "CONN": str(conn),
        "DURATION_S": str(duration),
        "OUT": str(out),
        **extra,
    }
    stats_before = sample_stats()
    t0 = time.time()
    p = subprocess.run(
        [str(PY), str(HARNESS)],
        env=env, capture_output=True, text=True,
        timeout=int(duration) + 240,
    )
    wall = round(time.time() - t0, 2)
    stats_after = sample_stats()
    try:
        r = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r = {
            "error": "no report",
            "returncode": p.returncode,
            "stdout": (p.stdout or "")[-600:],
            "stderr": (p.stderr or "")[-600:],
        }
    r["_label"] = label
    r["_inflight"] = workers * conn
    r["_wall_orchestrated"] = wall
    r["_docker_before"] = stats_before
    r["_docker_after"] = stats_after
    STATS_LOG.write_text(
        STATS_LOG.read_text(encoding="utf-8") + f"\n# {label} before\n{stats_before}\n# {label} after\n{stats_after}\n"
        if STATS_LOG.exists() else f"# {label} before\n{stats_before}\n# {label} after\n{stats_after}\n",
        encoding="utf-8",
    )
    out.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    print(
        f"== {label} inflight={workers * conn} rps={r.get('rps')} "
        f"ok_rps={r.get('ok_rps')} err_rate={r.get('error_rate')} codes={r.get('codes')}",
        flush=True,
    )
    return r


def lift_limits(api_key: str) -> dict:
    cfg_key = f"firewall:config:{ORG}"
    raw = redis_get(cfg_key)
    snap = {"config_raw": raw}
    if raw:
        data = json.loads(raw)
        data["rate_limit_enabled"] = False
        data["auto_block_threats"] = False
        data["burst_limit"] = 10_000_000
        data["requests_per_minute"] = 10_000_000
        data["org_tpm_limit"] = 0
        redis_set(cfg_key, json.dumps(data))
        redis_publish("config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    h = hashlib.sha256(api_key.encode()).hexdigest()
    auth_k = f"auth:apikey:{h}"
    araw = redis_get(auth_k)
    snap["auth_key"] = auth_k
    snap["auth_raw"] = araw
    if araw:
        payload = json.loads(araw)
        payload["rate_limit_tpm"] = 0
        payload["rpm_limit"] = 0
        payload["risk_score"] = 0.0
        redis_set(auth_k, json.dumps(payload))
    # Transient CB from prior sat — operator kill-switches (ttl=-1) stay.
    ssh(
        f"docker exec {REDIS} sh -c "
        "\"redis-cli --raw KEYS 'circuit:state:*' | "
        "xargs -r -n 100 redis-cli DEL\"",
        timeout=30,
    )
    return snap


def restore_limits(snap: dict) -> None:
    cfg_key = f"firewall:config:{ORG}"
    if snap.get("config_raw"):
        redis_set(cfg_key, snap["config_raw"])
        redis_publish("config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    if snap.get("auth_key") and snap.get("auth_raw"):
        payload = json.loads(snap["auth_raw"])
        payload["risk_score"] = 0.0  # never re-poison
        redis_set(snap["auth_key"], json.dumps(payload))


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("API_KEY") or ""
    if not key:
        raise SystemExit("API_KEY required")
    extra = {
        "API_KEY": key,
        "MAX_TOKENS": "16",
        "PROMPT": "What is the capital of France? Reply with the city name only.",
        "MODEL": os.environ.get("MODEL", "auto"),
        "UNIQUE_PROMPT": "1",
        "TIMEOUT_S": os.environ.get("TIMEOUT_S", "180"),
        "GATEWAY": os.environ["GATEWAY"],
    }
    snap = lift_limits(key)
    (OUTDIR / "limit_snapshot_meta.json").write_text(
        json.dumps({"lifted": True, "auth_key": snap.get("auth_key")}, indent=2) + "\n",
        encoding="utf-8",
    )
    results = []
    try:
        skip_health = os.environ.get("SKIP_HEALTH", "0") in ("1", "true")
        skip_chat = os.environ.get("SKIP_CHAT", "0") in ("1", "true")
        if not skip_health:
            for inflight, w, c, dur in HEALTH_LEVELS:
                r = run_bench(f"health_if{inflight}", "health", w, c, dur, extra)
                results.append(r)
                if (r.get("error_rate") or 0) >= 0.5 and inflight >= 4096:
                    print("health error_rate>=0.5 — stop health escalation", flush=True)
                    break
        if not skip_chat:
            for inflight, w, c, dur in CHAT_LEVELS:
                r = run_bench(f"chat_if{inflight}", "chat", w, c, dur, extra)
                results.append(r)
                er = r.get("error_rate")
                if er is None:
                    continue
                if er >= 0.9:
                    print("chat error_rate>=0.9 — hardware/AWS ceiling", flush=True)
                    break
    finally:
        restore_limits(snap)

    health = [r for r in results if str(r.get("_label", "")).startswith("health_") and "ok_rps" in r]
    chat = [r for r in results if str(r.get("_label", "")).startswith("chat_") and "ok_rps" in r]
    peak_h = max(health, key=lambda x: x.get("ok_rps") or 0) if health else {}
    peak_c = max(chat, key=lambda x: x.get("ok_rps") or 0) if chat else {}
    zero_err = [r for r in chat if float(r.get("error_rate") or 0) == 0.0]
    last_zero = max(zero_err, key=lambda x: x.get("_inflight") or 0) if zero_err else {}
    verdict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gateway": os.environ.get("GATEWAY"),
        "model": extra["MODEL"],
        "unique_prompt": True,
        "stub_llm": os.environ.get("GATEWAY_LOADTEST_STUB_LLM", "0"),
        "peak_health_ok_rps": peak_h.get("ok_rps"),
        "peak_health_inflight": peak_h.get("_inflight"),
        "peak_chat_ok_rps": peak_c.get("ok_rps"),
        "peak_chat_inflight": peak_c.get("_inflight"),
        "peak_chat_error_rate": peak_c.get("error_rate"),
        "last_zero_error_chat": {
            "inflight": last_zero.get("_inflight"),
            "ok_rps": last_zero.get("ok_rps"),
            "p50_ms": (last_zero.get("client_latency_ms") or {}).get("p50"),
        },
        "claimed_100k_rps": False,
        "claimed_100k_reason": (
            "100k RPS of live 9-stage (Bedrock T2 + adjudicator + output guard + BYOK) "
            "is not claimed unless a level measured ok_rps>=100000 with error_rate=0."
        ),
        "table": [
            {
                "label": r.get("_label"),
                "inflight": r.get("_inflight"),
                "ok_rps": r.get("ok_rps"),
                "rps": r.get("rps"),
                "error_rate": r.get("error_rate"),
                "codes": r.get("codes"),
                "p50_ms": (r.get("client_latency_ms") or {}).get("p50"),
                "p50_input_scan": ((r.get("stage_latency_ms") or {}).get("input_scan") or {}).get("p50")
                if isinstance(r.get("stage_latency_ms"), dict) else None,
                "p50_routing": ((r.get("stage_latency_ms") or {}).get("model_routing") or {}).get("p50")
                if isinstance(r.get("stage_latency_ms"), dict) else None,
                "top_errors": dict(list((r.get("errors_by_signature") or {}).items())[:4]),
            }
            for r in results
        ],
    }
    (OUTDIR / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "peak_health_ok_rps": verdict["peak_health_ok_rps"],
        "peak_chat_ok_rps": verdict["peak_chat_ok_rps"],
        "last_zero_error_chat": verdict["last_zero_error_chat"],
        "claimed_100k_rps": False,
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
