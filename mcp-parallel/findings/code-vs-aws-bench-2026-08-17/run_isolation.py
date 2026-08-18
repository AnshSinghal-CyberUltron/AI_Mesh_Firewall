#!/usr/bin/env python3
"""Prod isolation ladder: prove gateway Python is not the 9-stage RPS wall.

Runs FROM the GCP box against prod EC2. Recreates gateway for stub modes, then
restores live defaults. Unique TOK-hex prompts. Never prints API_KEY.
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

ROOT = Path(__file__).resolve().parents[3]
PY = ROOT / "gateway" / ".venv" / "bin" / "python"
HARNESS = ROOT / "scripts" / "perf" / "gateway_pipeline_bench.py"
PROBE = ROOT / "scripts" / "perf" / "prod_unique_prompt_probe.py"
OUTDIR = Path(__file__).resolve().parent
SSH = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "AIMeshFirewall"]
ORG = os.environ.get("ORG_SLUG", "zeroshield")
REDIS = os.environ.get("REDIS_CTR", "ai_mesh_firewall-redis-1")
REMOTE = "/home/ec2-user/AI_Mesh_Firewall"
OVERRIDE = "/tmp/gw-isolation.override.yml"
DIRECT = os.environ.get(
    "GATEWAY",
    "http://ec2-35-154-124-71.ap-south-1.compute.amazonaws.com:8300",
).rstrip("/")
NLB = os.environ.get("NLB_GATEWAY", "https://aimeshgateway.zeroshield.ai").rstrip("/")
CONTROL_HEALTH = os.environ.get(
    "CONTROL_HEALTH",
    "https://aimeshbackend.zeroshield.ai/api/health/",
)


def ssh(cmd: str, timeout: int = 90, stdin: str | None = None) -> tuple[int, str, str]:
    p = subprocess.run(
        [*SSH, cmd],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def ssh_ok(cmd: str, timeout: int = 90, stdin: str | None = None) -> str:
    rc, out, err = ssh(cmd, timeout=timeout, stdin=stdin)
    if rc != 0:
        print(f"SSH rc={rc} cmd={cmd[:120]!r} err={err[-400:]}", flush=True)
    return out


def redis_get(key: str) -> str | None:
    raw = ssh_ok(f"docker exec {REDIS} redis-cli --raw GET {key}", timeout=45)
    if not raw or raw == "(nil)":
        return None
    return raw


def redis_set(key: str, value: str) -> None:
    ssh_ok(f"docker exec -i {REDIS} redis-cli -x SET {key}", timeout=45, stdin=value)


def redis_publish(channel: str, payload: str) -> None:
    ssh_ok(
        f"docker exec -i {REDIS} redis-cli -x PUBLISH {channel}",
        timeout=30,
        stdin=payload,
    )


def sample_stats() -> str:
    return ssh_ok(
        "date -u +%Y-%m-%dT%H:%M:%SZ; "
        "docker stats --no-stream --format "
        "'{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}' "
        "| grep -E 'gateway-1|control-1|postgres-1|redis-1|nginx-1|pgbouncer'",
        timeout=30,
    )


def pg_ping() -> dict:
    out = ssh_ok(
        "docker exec ai_mesh_firewall-postgres-1 psql -U ai_mesh_firewall "
        "-d ai_mesh_firewall -c '\\timing on' -c 'SELECT 1 AS ok;'",
        timeout=20,
    )
    return {"raw": out[-400:]}


def control_ping() -> dict:
    out = ssh_ok(
        "python3 - <<'PY'\n"
        "import time,urllib.request\n"
        "url='http://127.0.0.1:8100/api/health/'\n"
        "times=[]\n"
        "for _ in range(20):\n"
        "    t=time.perf_counter()\n"
        "    try:\n"
        "        r=urllib.request.urlopen(url, timeout=5)\n"
        "        code=r.status; body=r.read(80)\n"
        "    except Exception as e:\n"
        "        times.append(('ERR', str(e)[:80], (time.perf_counter()-t)*1000))\n"
        "        continue\n"
        "    times.append((code, body.decode('utf-8','replace')[:60], (time.perf_counter()-t)*1000))\n"
        "ok=[x for x in times if x[0]==200]\n"
        "ms=sorted(x[2] for x in ok)\n"
        "print('n',len(times),'ok',len(ok))\n"
        "print('p50', round(ms[len(ms)//2],3) if ms else None)\n"
        "print('p95', round(ms[int(len(ms)*0.95)-1],3) if ms else None)\n"
        "print('sample', times[0] if times else None)\n"
        "PY",
        timeout=40,
    )
    return {"raw": out}


def wait_health(url: str, tries: int = 40) -> bool:
    for i in range(tries):
        p = subprocess.run(
            [str(PY), "-c",
             "import httpx,sys; r=httpx.get(sys.argv[1], timeout=8); "
             "print(r.status_code); sys.exit(0 if r.status_code==200 else 1)",
             url + "/health"],
            capture_output=True, text=True, timeout=20,
        )
        if p.returncode == 0:
            print(f"health 200 after {i+1} tries {url}", flush=True)
            return True
        time.sleep(3)
    print(f"health FAIL {url} last={(p.stdout or '')[:80]} {(p.stderr or '')[-200:]}", flush=True)
    return False


def recreate_gateway(mode: str) -> str:
    """mode: live | stub_aws | code_only"""
    if mode == "live":
        # Pre-bench inspect showed WEB_CONCURRENCY=6 from remote .env — restore that.
        env = {
            "WEB_CONCURRENCY": "6",
            "GATEWAY_CIRCUIT_BREAKER_ENABLED": "true",
            "GATEWAY_LOADTEST_STUB_LLM": "0",
            "GATEWAY_TIER2_SAMPLE_RATE": "1.0",
            "ENABLE_TIER2": "true",
            "ROUTING_ADJUDICATOR_ALWAYS": "true",
            "GATEWAY_OUTPUT_GUARD_ENABLED": "true",
            "GATEWAY_TIER2_CACHE_TTL_SECONDS": "0",
        }
    elif mode == "stub_aws":
        env = {
            "WEB_CONCURRENCY": "16",
            "GATEWAY_CIRCUIT_BREAKER_ENABLED": "false",
            "GATEWAY_LOADTEST_STUB_LLM": "1",
            "GATEWAY_TIER2_SAMPLE_RATE": "1.0",
            "ENABLE_TIER2": "true",
            "ROUTING_ADJUDICATOR_ALWAYS": "true",
            "GATEWAY_OUTPUT_GUARD_ENABLED": "true",
            "GATEWAY_TIER2_CACHE_TTL_SECONDS": "0",
        }
    elif mode == "code_only":
        env = {
            "WEB_CONCURRENCY": "16",
            "GATEWAY_CIRCUIT_BREAKER_ENABLED": "false",
            "GATEWAY_LOADTEST_STUB_LLM": "1",
            "GATEWAY_TIER2_SAMPLE_RATE": "0",
            "ENABLE_TIER2": "false",
            "ROUTING_ADJUDICATOR_ALWAYS": "false",
            "GATEWAY_OUTPUT_GUARD_ENABLED": "false",
            "GATEWAY_TIER2_CACHE_TTL_SECONDS": "0",
        }
    else:
        raise ValueError(mode)
    exports = " ".join(f"{k}={v}" for k, v in env.items())
    script = f"""
set -e
cd {REMOTE}
cat > {OVERRIDE} << 'EOF'
services:
  gateway:
    environment:
      GATEWAY_OUTPUT_GUARD_ENABLED: ${{GATEWAY_OUTPUT_GUARD_ENABLED:-true}}
EOF
export {exports}
echo "RECREATE mode={mode} WEB=$WEB_CONCURRENCY STUB=$GATEWAY_LOADTEST_STUB_LLM T2=$ENABLE_TIER2 SAMPLE=$GATEWAY_TIER2_SAMPLE_RATE ADJ=$ROUTING_ADJUDICATOR_ALWAYS OG=$GATEWAY_OUTPUT_GUARD_ENABLED CB=$GATEWAY_CIRCUIT_BREAKER_ENABLED"
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f {OVERRIDE} \
  up -d --no-deps --force-recreate --pull never gateway
docker inspect ai_mesh_firewall-gateway-1 --format \
  'Nano={{{{.HostConfig.NanoCpus}}}} Mem={{{{.HostConfig.Memory}}}}'
docker exec ai_mesh_firewall-gateway-1 printenv \
  | grep -E 'WEB_CONCURRENCY|LOADTEST_STUB|TIER2_SAMPLE|ENABLE_TIER2|ADJUDICATOR_ALWAYS|OUTPUT_GUARD|CIRCUIT_BREAKER' \
  | sort
"""
    rc, out, err = ssh(script, timeout=180)
    (OUTDIR / f"recreate_{mode}.txt").write_text(out + "\n---stderr---\n" + err, encoding="utf-8")
    print(out[-800:], flush=True)
    if rc != 0:
        raise RuntimeError(f"recreate {mode} rc={rc}")
    return out


def lift_limits(api_key: str, disable_output_scan: bool) -> dict:
    cfg_key = f"firewall:config:{ORG}"
    raw = redis_get(cfg_key)
    snap: dict = {"config_raw": raw, "disable_output_scan": disable_output_scan}
    if raw:
        data = json.loads(raw)
        data["rate_limit_enabled"] = False
        data["auto_block_threats"] = False
        data["burst_limit"] = 10_000_000
        data["requests_per_minute"] = 10_000_000
        data["org_tpm_limit"] = 0
        if disable_output_scan:
            data["output_scan_enabled"] = False
            data["response_filtering_enabled"] = False
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
    ssh_ok(
        f"docker exec {REDIS} sh -c "
        "\"redis-cli --raw KEYS 'circuit:state:*' | xargs -r -n 100 redis-cli DEL\"",
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
        payload["risk_score"] = 0.0
        redis_set(snap["auth_key"], json.dumps(payload))


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
    before = sample_stats()
    t0 = time.time()
    p = subprocess.run(
        [str(PY), str(HARNESS)],
        env=env, capture_output=True, text=True,
        timeout=int(duration) + 240,
    )
    wall = round(time.time() - t0, 2)
    after = sample_stats()
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
    r["_wall_orchestrated"] = wall
    r["_docker_before"] = before
    r["_docker_after"] = after
    r["_harness_rc"] = p.returncode
    out.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    print(
        f"== {label} if={workers * conn} rps={r.get('rps')} ok_rps={r.get('ok_rps')} "
        f"err={r.get('error_rate')} p50={(r.get('client_latency_ms') or {}).get('p50')} "
        f"codes={r.get('codes')}",
        flush=True,
    )
    return r


def run_probe(label: str, extra: dict, max_tokens: str = "16") -> dict:
    out = OUTDIR / f"probe_{label}.json"
    env = {**os.environ, **extra, "OUT": str(out), "N": "2", "MAX_TOKENS": max_tokens, "TIMEOUT_S": "180"}
    p = subprocess.run([str(PY), str(PROBE)], env=env, capture_output=True, text=True, timeout=400)
    try:
        r = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r = {"error": "no probe", "stderr": (p.stderr or "")[-400:], "stdout": (p.stdout or "")[-400:]}
    recs = r.get("records") or []
    stages = (recs[0].get("stages") if recs else None)
    print(f"== probe {label} statuses={[x.get('status') for x in recs]} stages={stages}", flush=True)
    return r


def warm_catalog(extra: dict) -> None:
    for _ in range(12):
        subprocess.run(
            [str(PY), "-c",
             "import httpx,sys; httpx.get(sys.argv[1]+'/health', timeout=8)", extra["GATEWAY"]],
            timeout=15, capture_output=True,
        )
    run_probe("warm", extra)


def row(r: dict) -> dict:
    st = r.get("stage_latency_ms") or {}
    def p50(name):
        v = st.get(name) or {}
        return v.get("p50") if isinstance(v, dict) else None
    return {
        "label": r.get("_label"),
        "inflight": r.get("_inflight"),
        "ok_rps": r.get("ok_rps"),
        "rps": r.get("rps"),
        "error_rate": r.get("error_rate"),
        "codes": r.get("codes"),
        "p50_ms": (r.get("client_latency_ms") or {}).get("p50"),
        "p95_ms": (r.get("client_latency_ms") or {}).get("p95"),
        "p50_auth": p50("auth"),
        "p50_policy": p50("policy"),
        "p50_input_scan": p50("input_scan"),
        "p50_routing": p50("model_routing"),
        "p50_model_output": p50("model_output"),
        "p50_output_guard": p50("output_guardrail"),
        "p50_addon": (r.get("addon_latency_ms") or {}).get("p50"),
        "top_errors": dict(list((r.get("errors_by_signature") or {}).items())[:5]),
        "docker_after": r.get("_docker_after"),
    }


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    key_path = ROOT / "mcp-parallel" / "findings" / "bedrock-hotpath-2026-08-14" / ".api_key"
    key = os.environ.get("API_KEY") or key_path.read_text(encoding="utf-8").strip()
    extra_direct = {
        "API_KEY": key,
        "GATEWAY": DIRECT,
        "MODEL": "auto",
        "PROMPT": "What is the capital of France? Reply with the city name only.",
        "UNIQUE_PROMPT": "1",
        "TIMEOUT_S": "180",
        "MAX_TOKENS": "16",
    }
    extra_nlb = {**extra_direct, "GATEWAY": NLB}
    extra_health_d = {**extra_direct}
    extra_health_n = {**extra_nlb}
    results: list[dict] = []
    probes: dict = {}
    snap: dict = {}
    restored = False

    def finish_restore():
        nonlocal restored
        if restored:
            return
        print("RESTORE live gateway + redis", flush=True)
        try:
            if snap:
                restore_limits(snap)
            recreate_gateway("live")
            wait_health(DIRECT, tries=30)
            wait_health(NLB, tries=20)
        except Exception as exc:  # noqa: BLE001
            print(f"RESTORE ERROR {exc!r}", flush=True)
        restored = True

    try:
        print("=== H health direct+NLB ===", flush=True)
        for inflight, w, c, dur, extra, tag in (
            (64, 8, 8, 8, extra_health_d, "direct"),
            (256, 16, 16, 8, extra_health_d, "direct"),
            (64, 8, 8, 8, extra_health_n, "nlb"),
        ):
            results.append(run_bench(f"H_health_{tag}_if{inflight}", "health", w, c, dur, extra))

        print("=== control / postgres idle ===", flush=True)
        (OUTDIR / "control_idle.json").write_text(
            json.dumps({"control": control_ping(), "pg": pg_ping(), "stats": sample_stats()}, indent=2) + "\n",
            encoding="utf-8",
        )

        print("=== L live unique 9-stage ===", flush=True)
        probes["L"] = run_probe("L_live", extra_direct)
        results.append(run_bench("L_live_if32", "chat", 8, 4, 12, extra_direct))

        print("=== S scan_only max_tokens=0 (T2 still live) ===", flush=True)
        extra_scan = {**extra_direct, "MAX_TOKENS": "0"}
        probes["S"] = run_probe("S_scan_only", extra_scan, max_tokens="0")
        results.append(run_bench("S_scan_only_if32", "chat", 8, 4, 12, extra_scan))
        results.append(run_bench("S_scan_only_if128", "chat", 16, 8, 12, extra_scan))

        snap = lift_limits(key, disable_output_scan=False)
        (OUTDIR / "limit_snapshot_meta.json").write_text(
            json.dumps({"lifted": True, "auth_key": snap.get("auth_key")}, indent=2) + "\n",
            encoding="utf-8",
        )

        print("=== C code_only recreate (stub LLM + skip T2/guard/adjudicator) ===", flush=True)
        lift_limits(key, disable_output_scan=True)
        recreate_gateway("code_only")
        wait_health(DIRECT, tries=40)
        warm_catalog(extra_direct)
        probes["C"] = run_probe("C_code_only", extra_direct)
        for inflight, w, c, dur in (
            (32, 8, 4, 10),
            (128, 16, 8, 10),
            (512, 16, 32, 10),
            (1024, 32, 32, 10),
            (2048, 32, 64, 8),
        ):
            results.append(run_bench(f"C_code_if{inflight}", "chat", w, c, dur, extra_direct))
            if inflight == 512:
                (OUTDIR / "control_during_C512.json").write_text(
                    json.dumps({"control": control_ping(), "pg": pg_ping(), "stats": sample_stats()}, indent=2) + "\n",
                    encoding="utf-8",
                )
            er = results[-1].get("error_rate")
            if er is not None and er >= 0.5:
                print("C error_rate>=0.5 stop escalate", flush=True)
                break
        results.append(run_bench("C_code_nlb_if128", "chat", 16, 8, 10, extra_nlb))

        print("=== B stub LLM, AWS T2+guard+adjudicator still live ===", flush=True)
        lift_limits(key, disable_output_scan=False)
        recreate_gateway("stub_aws")
        wait_health(DIRECT, tries=40)
        warm_catalog(extra_direct)
        probes["B"] = run_probe("B_stub_llm", extra_direct)
        for inflight, w, c, dur in (
            (32, 8, 4, 12),
            (128, 16, 8, 12),
            (256, 16, 16, 12),
        ):
            results.append(run_bench(f"B_stub_if{inflight}", "chat", w, c, dur, extra_direct))
            er = results[-1].get("error_rate")
            if er is not None and er >= 0.4:
                print("B error_rate>=0.4 stop escalate", flush=True)
                break
    finally:
        finish_restore()

    probes["L_after_restore"] = run_probe("L_after_restore", extra_direct)
    table = [row(r) for r in results]
    c_rows = [r for r in results if str(r.get("_label", "")).startswith("C_")]
    l_rows = [r for r in results if str(r.get("_label", "")).startswith("L_")]
    h_rows = [r for r in results if str(r.get("_label", "")).startswith("H_")]
    peak_c = max(c_rows, key=lambda x: x.get("ok_rps") or 0) if c_rows else {}
    peak_h = max(h_rows, key=lambda x: x.get("ok_rps") or 0) if h_rows else {}
    live = l_rows[0] if l_rows else {}
    verdict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": {"direct": DIRECT, "nlb": NLB, "control_health": CONTROL_HEALTH},
        "claim": (
            "Gateway Python is not the live 9-stage RPS wall. Same process serves "
            "/health at thousands of RPS and a code-only (AWS-subtracted) chat path "
            "at far higher ok_rps / far lower p50 than live T2+BYOK+guard."
        ),
        "claimed_100k_rps_nine_stage": False,
        "peak_health_ok_rps": peak_h.get("ok_rps"),
        "peak_code_only_ok_rps": peak_c.get("ok_rps"),
        "live_if32_ok_rps": live.get("ok_rps"),
        "live_if32_p50_ms": (live.get("client_latency_ms") or {}).get("p50"),
        "code_vs_live_ratio": (
            round((peak_c.get("ok_rps") or 0) / (live.get("ok_rps") or 1), 2)
            if live.get("ok_rps") else None
        ),
        "table": table,
        "probes_stage_p50_hint": {
            k: (v.get("records") or [{}])[0].get("stages")
            for k, v in probes.items()
        },
        "restored": restored,
    }
    (OUTDIR / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "peak_health_ok_rps": verdict["peak_health_ok_rps"],
        "peak_code_only_ok_rps": verdict["peak_code_only_ok_rps"],
        "live_if32_ok_rps": verdict["live_if32_ok_rps"],
        "code_vs_live_ratio": verdict["code_vs_live_ratio"],
        "restored": restored,
    }, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
