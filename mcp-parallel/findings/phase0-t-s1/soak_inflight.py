#!/usr/bin/env python3
"""Bounded T-S1 soak: MB per in-flight stub stream (NOT a capacity RPS).

Spawns a sidecar gateway on 127.0.0.1:18300 with token-emitting stub (3s, 50 tok/s),
WEB_CONCURRENCY=1, AUTH off, Tier-2 off. Caps concurrency. Aborts on OOM / error spike.
Does not recreate the shared compose gateway.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parent
SIDECAR = "phase0-t-s1-gw"
PORT = 18300
GATEWAY = f"http://127.0.0.1:{PORT}"
INFLIGHT = int(os.environ.get("T_S1_INFLIGHT", "4"))
STUB_S = float(os.environ.get("GATEWAY_LOADTEST_STUB_DURATION_S", "3"))
WAVES = int(os.environ.get("T_S1_WAVES", "3"))
SAMPLE_S = 0.4
MEM_LIMIT = os.environ.get("T_S1_MEM_LIMIT", "1g")
ERROR_ABORT = 0.2


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, text=True, capture_output=True, **kw)


def _cgroup_bytes(name: str) -> int | None:
    r = _run(["docker", "exec", name, "sh", "-c",
              "cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes"])
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip().split()[0])
    except (TypeError, ValueError):
        return None


def _docker_stats_mb(name: str) -> dict:
    r = _run(["docker", "stats", name, "--no-stream", "--format",
              "{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"])
    raw = (r.stdout or "").strip()
    return {"raw": raw, "cgroup_bytes": _cgroup_bytes(name)}


def _sidecar_alive() -> bool:
    r = _run(["docker", "inspect", "-f", "{{.State.Running}} {{.State.OOMKilled}}", SIDECAR])
    return (r.stdout or "").startswith("true")


def _write_env(path: Path) -> None:
    ins = json.loads(_run(["docker", "inspect", "ai_mesh_firewall-gateway-1"]).stdout)[0]
    skip = ("HOSTNAME=", "HOME=", "PATH=", "PWD=", "TERM=")
    lines = []
    for e in ins["Config"]["Env"]:
        if e.startswith(skip):
            continue
        key = e.split("=", 1)[0]
        if key in {
            "WEB_CONCURRENCY", "GATEWAY_LOADTEST_STUB_LLM",
            "GATEWAY_LOADTEST_STUB_DURATION_S", "GATEWAY_LOADTEST_STUB_TOK_PER_S",
            "GATEWAY_AUTH_ENABLED", "ENABLE_TIER2", "PROMETHEUS_MULTIPROC_DIR",
        }:
            continue
        lines.append(e)
    lines.extend([
        "WEB_CONCURRENCY=1",
        "GATEWAY_LOADTEST_STUB_LLM=1",
        f"GATEWAY_LOADTEST_STUB_DURATION_S={STUB_S:g}",
        "GATEWAY_LOADTEST_STUB_TOK_PER_S=50",
        "GATEWAY_AUTH_ENABLED=false",
        "ENABLE_TIER2=false",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def start_sidecar(env_path: Path) -> None:
    _run(["docker", "rm", "-f", SIDECAR])
    cmd = [
        "docker", "run", "-d", "--name", SIDECAR,
        "--network", "ai_mesh_firewall_default",
        "-p", f"127.0.0.1:{PORT}:8300",
        "--memory", MEM_LIMIT, "--cpus", "1",
        "--env-file", str(env_path),
        "-v", f"{ROOT}/gateway/ai_mesh_gateway:/app/gateway/ai_mesh_gateway:ro",
        "ai_mesh_firewall-gateway",
    ]
    r = _run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"sidecar start failed: {r.stderr}")


def wait_health(timeout_s: float = 45.0) -> None:
    t0 = time.monotonic()
    last = ""
    while time.monotonic() - t0 < timeout_s:
        if not _sidecar_alive():
            logs = _run(["docker", "logs", "--tail", "40", SIDECAR])
            raise SystemExit(f"sidecar died: oom={logs.stderr}\n{logs.stdout}")
        try:
            r = httpx.get(f"{GATEWAY}/health", timeout=2.0)
            last = f"{r.status_code} {r.text[:120]}"
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last = type(exc).__name__
        time.sleep(0.5)
    raise SystemExit(f"sidecar health timeout: {last}")


async def _one_stream(client: httpx.AsyncClient) -> dict:
    t0 = time.perf_counter()
    n_bytes = 0
    status = 0
    err = None
    stub_id = False
    try:
        async with client.stream(
            "POST",
            f"{GATEWAY}/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "say ok"}],
                "max_tokens": 200,
                "stream": True,
                "enable_routing": False,
            },
        ) as r:
            status = r.status_code
            async for chunk in r.aiter_bytes():
                n_bytes += len(chunk)
                if b"chatcmpl-loadtest-stub" in chunk:
                    stub_id = True
            if status >= 400:
                err = f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001
        err = type(exc).__name__
    return {
        "ok": err is None and 200 <= status < 300,
        "status": status,
        "bytes": n_bytes,
        "wall_s": round(time.perf_counter() - t0, 3),
        "stub_id": stub_id,
        "err": err,
    }


async def soak(samples: list[dict]) -> dict:
    stop = False

    async def sampler():
        while not stop:
            row = _docker_stats_mb(SIDECAR)
            row["t"] = round(time.time(), 3)
            row["alive"] = _sidecar_alive()
            samples.append(row)
            if not row["alive"]:
                return
            await asyncio.sleep(SAMPLE_S)

    timeout = httpx.Timeout(STUB_S + 15.0, connect=5.0)
    limits = httpx.Limits(max_connections=INFLIGHT, max_keepalive_connections=INFLIGHT)
    results = []
    samp_task = asyncio.create_task(sampler())
    try:
        async with httpx.AsyncClient(timeout=timeout, limits=limits, http2=False) as client:
            for _ in range(WAVES):
                if not _sidecar_alive():
                    break
                wave = await asyncio.gather(*[_one_stream(client) for _ in range(INFLIGHT)])
                results.extend(wave)
                errs = sum(1 for x in wave if not x["ok"])
                if INFLIGHT and (errs / INFLIGHT) > ERROR_ABORT:
                    break
    finally:
        stop = True
        samp_task.cancel()
        try:
            await samp_task
        except (asyncio.CancelledError, Exception):
            pass
    n = len(results)
    ok = sum(1 for x in results if x["ok"])
    return {
        "requests": n,
        "ok": ok,
        "errors": n - ok,
        "error_rate": round((n - ok) / n, 4) if n else None,
        "stub_id_seen": sum(1 for x in results if x["stub_id"]),
        "walls_s": [x["wall_s"] for x in results],
        "bytes": [x["bytes"] for x in results],
        "results": results,
    }


def summarize(idle: list[dict], soak_samples: list[dict], soak_stats: dict) -> dict:
    def mb(rows):
        vals = [r["cgroup_bytes"] / (1024 * 1024) for r in rows if r.get("cgroup_bytes")]
        return vals

    idle_mb = mb(idle)
    soak_mb = mb(soak_samples)
    idle_med = sorted(idle_mb)[len(idle_mb) // 2] if idle_mb else None
    peak = max(soak_mb) if soak_mb else None
    delta = (peak - idle_med) if (peak is not None and idle_med is not None) else None
    per = (delta / INFLIGHT) if (delta is not None and INFLIGHT) else None
    oom = False
    r = _run(["docker", "inspect", "-f", "{{.State.OOMKilled}} {{.State.ExitCode}}", SIDECAR])
    parts = (r.stdout or "").split()
    if parts and parts[0].lower() == "true":
        oom = True
    return {
        "not_a_capacity_rps": True,
        "capacity_eligible": False,
        "capacity_fail_reasons": ["stub_llm", "sidecar_auth_off", "tier2_off", "not_shared_compose_gateway"],
        "method": {
            "sidecar": SIDECAR,
            "image": "ai_mesh_firewall-gateway + bind-mount working-tree ai_mesh_gateway",
            "network": "ai_mesh_firewall_default (docker-compose local)",
            "port": PORT,
            "inflight": INFLIGHT,
            "stub_duration_s": STUB_S,
            "stub_tok_per_s": 50,
            "waves": WAVES,
            "web_concurrency": 1,
            "auth_enabled": False,
            "enable_tier2": False,
            "stream": True,
            "mem_limit": MEM_LIMIT,
            "formula": "(peak_cgroup_MB - idle_median_cgroup_MB) / inflight",
        },
        "idle_cgroup_mb": [round(x, 2) for x in idle_mb],
        "idle_median_mb": round(idle_med, 2) if idle_med is not None else None,
        "soak_cgroup_mb": [round(x, 2) for x in soak_mb],
        "peak_cgroup_mb": round(peak, 2) if peak is not None else None,
        "delta_mb": round(delta, 2) if delta is not None else None,
        "mb_per_in_flight_stream": round(per, 2) if per is not None else None,
        "oom_killed": oom,
        "sidecar_alive_end": _sidecar_alive(),
        "soak": {k: v for k, v in soak_stats.items() if k != "results"},
        "docker_stats_idle": idle,
        "docker_stats_soak": soak_samples,
        "shared_gateway_cgroup_mb_end": round((_cgroup_bytes("ai_mesh_firewall-gateway-1") or 0) / (1024 * 1024), 2),
    }


def main() -> int:
    env_path = Path("/tmp/phase0-t-s1.env")
    _write_env(env_path)
    start_sidecar(env_path)
    try:
        wait_health()
        idle = []
        for _ in range(6):
            idle.append(_docker_stats_mb(SIDECAR))
            time.sleep(0.3)
        soak_samples: list[dict] = []
        soak_stats = asyncio.run(soak(soak_samples))
        report = summarize(idle, soak_samples, soak_stats)
        abort = bool(report["oom_killed"] or not report["sidecar_alive_end"])
        if soak_stats.get("error_rate") is not None and soak_stats["error_rate"] > ERROR_ABORT:
            abort = True
        report["aborted"] = abort
        (OUT_DIR / "t_s1_inflight.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({
            "mb_per_in_flight_stream": report["mb_per_in_flight_stream"],
            "peak_cgroup_mb": report["peak_cgroup_mb"],
            "idle_median_mb": report["idle_median_mb"],
            "inflight": INFLIGHT,
            "aborted": abort,
            "oom_killed": report["oom_killed"],
            "error_rate": soak_stats.get("error_rate"),
            "stub_id_seen": soak_stats.get("stub_id_seen"),
            "not_a_capacity_rps": True,
        }, indent=2))
        return 1 if abort else 0
    finally:
        _run(["docker", "rm", "-f", SIDECAR])
        try:
            env_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
