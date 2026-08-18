#!/usr/bin/env python3
"""Prod c8g.2xlarge vs this GCP 16c/60GiB VM — full-uncap isolation.

Same harness, unique TOK-hex prompts. Recreates both gateways into AWS-bypass
(code-only) then restores. Never prints API keys.
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
REDIS = "ai_mesh_firewall-redis-1"
REMOTE = "/home/ec2-user/AI_Mesh_Firewall"
PROD_OVERRIDE = "/tmp/gw-isolation.override.yml"
GCP_OVERRIDE = "/tmp/gw-isolation-gcp.override.yml"
PROD_GW = "http://ec2-35-154-124-71.ap-south-1.compute.amazonaws.com:8300"
GCP_GW = "http://127.0.0.1:8300"
CONTROL_LOCAL = "http://127.0.0.1:8100"

HEALTH_LEVELS = [
    (64, 8, 8, 8),
    (256, 16, 16, 8),
    (1024, 32, 32, 8),
    (4096, 32, 128, 8),
    (8192, 32, 256, 8),
]
CODE_LEVELS = [
    (32, 8, 4, 10),
    (128, 16, 8, 10),
    (512, 16, 32, 10),
    (1024, 32, 32, 10),
    (2048, 32, 64, 8),
    (4096, 32, 128, 8),
]


def sh(cmd, timeout=60, cwd=None, env=None):
    p = subprocess.run(
        cmd if isinstance(cmd, list) else ["bash", "-lc", cmd],
        capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env, check=False,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def ssh(cmd: str, timeout: int = 90, stdin: str | None = None):
    p = subprocess.run(
        [*SSH, cmd], input=stdin, capture_output=True, text=True, timeout=timeout, check=False,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def redis_get(where: str, key: str) -> str | None:
    cmd = f"docker exec {REDIS} redis-cli --raw GET {key}"
    if where == "prod":
        _, out, _ = ssh(cmd, timeout=45)
    else:
        _, out, _ = sh(cmd, timeout=20)
    if not out or out == "(nil)":
        return None
    return out


def redis_set(where: str, key: str, value: str) -> None:
    cmd = f"docker exec -i {REDIS} redis-cli -x SET {key}"
    if where == "prod":
        ssh(cmd, timeout=45, stdin=value)
    else:
        subprocess.run(
            ["docker", "exec", "-i", REDIS, "redis-cli", "-x", "SET", key],
            input=value, text=True, timeout=20, check=False, capture_output=True,
        )


def redis_pub(where: str, channel: str, payload: str) -> None:
    if where == "prod":
        ssh(f"docker exec -i {REDIS} redis-cli -x PUBLISH {channel}", timeout=30, stdin=payload)
    else:
        subprocess.run(
            ["docker", "exec", "-i", REDIS, "redis-cli", "-x", "PUBLISH", channel],
            input=payload, text=True, timeout=15, check=False, capture_output=True,
        )


def sample_stats(where: str) -> str:
    cmd = (
        "date -u +%Y-%m-%dT%H:%M:%SZ; docker stats --no-stream --format "
        "'{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}' "
        "| grep -E 'gateway-1|control-1|postgres-1|redis-1|nginx-1|pgbouncer'"
    )
    if where == "prod":
        _, out, _ = ssh(cmd, timeout=30)
        return out
    _, out, _ = sh(cmd, timeout=20)
    return out


def inventory(where: str) -> dict:
    if where == "prod":
        _, out, _ = ssh(
            "echo NPROC=$(nproc); echo ARCH=$(uname -m); free -h | head -2; "
            "docker inspect ai_mesh_firewall-gateway-1 --format "
            "'Nano={{.HostConfig.NanoCpus}} Mem={{.HostConfig.Memory}}'; "
            "docker exec ai_mesh_firewall-gateway-1 nproc; "
            "docker exec ai_mesh_firewall-gateway-1 printenv WEB_CONCURRENCY; "
            "ulimit -n; cat /proc/sys/net/core/somaxconn",
            timeout=20,
        )
        return {"raw": out}
    _, out, _ = sh(
        "echo NPROC=$(nproc); echo ARCH=$(uname -m); free -h | head -2; "
        "docker inspect ai_mesh_firewall-gateway-1 --format "
        "'Nano={{.HostConfig.NanoCpus}} Mem={{.HostConfig.Memory}} CpuQuota={{.HostConfig.CpuQuota}}'; "
        "docker exec ai_mesh_firewall-gateway-1 nproc; "
        "docker exec ai_mesh_firewall-gateway-1 printenv WEB_CONCURRENCY",
        timeout=20,
    )
    return {"raw": out}


# Literal pins so env_file .env cannot shadow the sat / restore intent.
OVERRIDE_BYPASS = """services:
  gateway:
    environment:
      WEB_CONCURRENCY: "16"
      GATEWAY_LOADTEST_STUB_LLM: "1"
      GATEWAY_TIER2_SAMPLE_RATE: "0"
      ENABLE_TIER2: "false"
      ROUTING_ADJUDICATOR_ALWAYS: "false"
      GATEWAY_OUTPUT_GUARD_ENABLED: "false"
      GATEWAY_CIRCUIT_BREAKER_ENABLED: "false"
      GATEWAY_TIER2_CACHE_TTL_SECONDS: "0"
"""
OVERRIDE_LIVE_PROD = """services:
  gateway:
    environment:
      WEB_CONCURRENCY: "6"
      GATEWAY_LOADTEST_STUB_LLM: "0"
      GATEWAY_TIER2_SAMPLE_RATE: "1.0"
      ENABLE_TIER2: "true"
      ROUTING_ADJUDICATOR_ALWAYS: "true"
      GATEWAY_OUTPUT_GUARD_ENABLED: "true"
      GATEWAY_CIRCUIT_BREAKER_ENABLED: "true"
"""
OVERRIDE_LIVE_GCP = """services:
  gateway:
    environment:
      WEB_CONCURRENCY: "16"
      GATEWAY_LOADTEST_STUB_LLM: "0"
      GATEWAY_TIER2_SAMPLE_RATE: "1.0"
      ENABLE_TIER2: "true"
      ROUTING_ADJUDICATOR_ALWAYS: "true"
      GATEWAY_OUTPUT_GUARD_ENABLED: "true"
      GATEWAY_CIRCUIT_BREAKER_ENABLED: "true"
"""


def recreate(where: str, mode: str) -> str:
    if mode == "bypass":
        yaml = OVERRIDE_BYPASS
    elif where == "prod":
        yaml = OVERRIDE_LIVE_PROD
    else:
        yaml = OVERRIDE_LIVE_GCP
    if where == "prod":
        script = f"""
set -e
cd {REMOTE}
cat > {PROD_OVERRIDE} << 'EOF'
{yaml}
EOF
echo RECREATE where=prod mode={mode}
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f {PROD_OVERRIDE} \\
  up -d --no-deps --force-recreate --pull never gateway
docker exec ai_mesh_firewall-gateway-1 printenv | grep -E 'WEB_CONCURRENCY|LOADTEST_STUB|TIER2_SAMPLE|ENABLE_TIER2|ADJUDICATOR_ALWAYS|OUTPUT_GUARD|CIRCUIT_BREAKER' | sort
docker inspect ai_mesh_firewall-gateway-1 --format 'Nano={{{{.HostConfig.NanoCpus}}}} Mem={{{{.HostConfig.Memory}}}}'
"""
        rc, out, err = ssh(script, timeout=180)
    else:
        Path(GCP_OVERRIDE).write_text(yaml, encoding="utf-8")
        rc, out, err = sh(
            f"set -e; cd {ROOT}; echo RECREATE where=gcp mode={mode}; "
            f"docker compose -f docker-compose.yml -f {GCP_OVERRIDE} "
            "up -d --no-deps --force-recreate --pull never gateway; "
            "docker exec ai_mesh_firewall-gateway-1 printenv | "
            "grep -E 'WEB_CONCURRENCY|LOADTEST_STUB|TIER2_SAMPLE|ENABLE_TIER2|ADJUDICATOR_ALWAYS|OUTPUT_GUARD|CIRCUIT_BREAKER' | sort; "
            "docker inspect ai_mesh_firewall-gateway-1 --format 'Nano={{.HostConfig.NanoCpus}} Mem={{.HostConfig.Memory}}'",
            timeout=180,
        )
    (OUTDIR / f"recreate_{where}_{mode}.txt").write_text(out + "\n---\n" + err, encoding="utf-8")
    print(out[-900:], flush=True)
    if rc != 0:
        raise RuntimeError(f"recreate {where}/{mode} rc={rc} err={err[-400:]}")
    return out


def wait_health(url: str, tries: int = 40) -> bool:
    for i in range(tries):
        p = subprocess.run(
            [str(PY), "-c",
             "import httpx,sys; r=httpx.get(sys.argv[1], timeout=8); sys.exit(0 if r.status_code==200 else 1)",
             url.rstrip("/") + "/health"],
            capture_output=True, timeout=15,
        )
        if p.returncode == 0:
            print(f"health 200 {url} tries={i+1}", flush=True)
            return True
        time.sleep(2)
    print(f"health FAIL {url}", flush=True)
    return False


def lift(where: str, api_key: str, disable_og: bool) -> dict:
    cfg_key = f"firewall:config:{ORG}"
    raw = redis_get(where, cfg_key)
    snap: dict = {"config_raw": raw}
    if raw:
        data = json.loads(raw)
        data["rate_limit_enabled"] = False
        data["auto_block_threats"] = False
        data["burst_limit"] = 10_000_000
        data["requests_per_minute"] = 10_000_000
        data["org_tpm_limit"] = 0
        if disable_og:
            data["output_scan_enabled"] = False
            data["response_filtering_enabled"] = False
        redis_set(where, cfg_key, json.dumps(data))
        redis_pub(where, "config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    h = hashlib.sha256(api_key.encode()).hexdigest()
    auth_k = f"auth:apikey:{h}"
    araw = redis_get(where, auth_k)
    snap["auth_key"] = auth_k
    snap["auth_raw"] = araw
    if araw:
        payload = json.loads(araw)
        payload["rate_limit_tpm"] = 0
        payload["rpm_limit"] = 0
        payload["risk_score"] = 0.0
        redis_set(where, auth_k, json.dumps(payload))
    del_cmd = (
        f"docker exec {REDIS} sh -c "
        "\"redis-cli --raw KEYS 'circuit:state:*' | xargs -r -n 100 redis-cli DEL\""
    )
    if where == "prod":
        ssh(del_cmd, timeout=30)
    else:
        sh(del_cmd, timeout=20)
    return snap


def restore_limits(where: str, snap: dict) -> None:
    if snap.get("config_raw"):
        redis_set(where, f"firewall:config:{ORG}", snap["config_raw"])
        redis_pub(where, "config_updates", json.dumps({"action": "reload", "org_slug": ORG}))
    if snap.get("auth_key") and snap.get("auth_raw"):
        payload = json.loads(snap["auth_raw"])
        payload["risk_score"] = 0.0
        redis_set(where, snap["auth_key"], json.dumps(payload))


def run_bench(label: str, where: str, mode: str, workers: int, conn: int, duration: float, extra: dict) -> dict:
    out = OUTDIR / f"{label}.json"
    env = {**os.environ, "MODE": mode, "WORKERS": str(workers), "CONN": str(conn),
           "DURATION_S": str(duration), "OUT": str(out), **extra}
    before = sample_stats(where)
    t0 = time.time()
    p = subprocess.run([str(PY), str(HARNESS)], env=env, capture_output=True, text=True,
                       timeout=int(duration) + 240)
    wall = round(time.time() - t0, 2)
    after = sample_stats(where)
    try:
        r = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r = {"error": "no report", "stderr": (p.stderr or "")[-600:], "stdout": (p.stdout or "")[-400:]}
    r["_label"] = label
    r["_where"] = where
    r["_inflight"] = workers * conn
    r["_wall"] = wall
    r["_docker_before"] = before
    r["_docker_after"] = after
    out.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    print(
        f"== {label} ok_rps={r.get('ok_rps')} err={r.get('error_rate')} "
        f"p50={(r.get('client_latency_ms') or {}).get('p50')} codes={r.get('codes')}",
        flush=True,
    )
    return r


def run_probe(label: str, extra: dict) -> dict:
    out = OUTDIR / f"probe_{label}.json"
    env = {**os.environ, **extra, "OUT": str(out), "N": "2", "MAX_TOKENS": "16", "TIMEOUT_S": "180"}
    subprocess.run([str(PY), str(PROBE)], env=env, capture_output=True, text=True, timeout=400)
    try:
        r = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r = {"error": "no probe"}
    recs = r.get("records") or []
    print(f"== probe {label} {[x.get('status') for x in recs]} "
          f"stages={(recs[0].get('stages') if recs else None)}", flush=True)
    return r


def ensure_gcp_key() -> str:
    path = OUTDIR / ".gcp_api_key"
    if path.exists() and path.stat().st_size > 20:
        return path.read_text(encoding="utf-8").strip()
    email = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
    password = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
    code = r"""
import json,os,sys,httpx
base=os.environ['CONTROL']
email=os.environ['EMAIL']; password=os.environ['PASSWORD']
c=httpx.Client(timeout=30)
r=c.post(base+'/api/auth/token/', json={'email':email,'password':password})
r.raise_for_status()
tok=r.json()['access']
h={'Authorization':'Bearer '+tok,'Content-Type':'application/json'}
r=c.post(base+'/api/gateways/keys/', headers=h, json={
    'name':'fullcap-isolation-2026-08-17','project_id':'fullcap-bench',
    'rate_limit_tokens_per_minute':0,'risk_score':0.0,'allowed_models':[],
})
r.raise_for_status()
key=r.json().get('key') or r.json().get('api_key')
if not key: raise SystemExit('no key in create response')
sys.stdout.write(key)
"""
    p = subprocess.run(
        [str(PY), "-c", code],
        env={**os.environ, "CONTROL": CONTROL_LOCAL, "EMAIL": email, "PASSWORD": password},
        capture_output=True, text=True, timeout=40,
    )
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise RuntimeError(f"gcp key create failed: {(p.stderr or '')[-500:]}")
    key = p.stdout.strip()
    path.write_text(key + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return key


def row(r: dict) -> dict:
    st = r.get("stage_latency_ms") or {}
    def p50(n):
        v = st.get(n) or {}
        return v.get("p50") if isinstance(v, dict) else None
    return {
        "label": r.get("_label"), "where": r.get("_where"), "inflight": r.get("_inflight"),
        "ok_rps": r.get("ok_rps"), "rps": r.get("rps"), "error_rate": r.get("error_rate"),
        "codes": r.get("codes"),
        "p50_ms": (r.get("client_latency_ms") or {}).get("p50"),
        "p95_ms": (r.get("client_latency_ms") or {}).get("p95"),
        "p50_input_scan": p50("input_scan"), "p50_policy": p50("policy"),
        "p50_model_output": p50("model_output"), "p50_guard": p50("output_guardrail"),
        "top_errors": dict(list((r.get("errors_by_signature") or {}).items())[:4]),
        "docker_after": r.get("_docker_after"),
    }


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    prod_key = (ROOT / "mcp-parallel/findings/bedrock-hotpath-2026-08-14/.api_key").read_text().strip()
    gcp_key = ensure_gcp_key()
    inv = {"prod": inventory("prod"), "gcp": inventory("gcp")}
    (OUTDIR / "inventory.json").write_text(json.dumps(inv, indent=2) + "\n", encoding="utf-8")
    extra_prod = {
        "API_KEY": prod_key, "GATEWAY": PROD_GW, "MODEL": "auto", "UNIQUE_PROMPT": "1",
        "PROMPT": "What is the capital of France? Reply with the city name only.",
        "TIMEOUT_S": "180", "MAX_TOKENS": "16",
    }
    extra_gcp = {**extra_prod, "API_KEY": gcp_key, "GATEWAY": GCP_GW}
    results: list[dict] = []
    probes: dict = {}
    snap_prod: dict = {}
    snap_gcp: dict = {}
    restored = {"prod": False, "gcp": False}

    def do_restore():
        print("RESTORE both gateways", flush=True)
        try:
            if snap_prod:
                restore_limits("prod", snap_prod)
            recreate("prod", "live")
            wait_health(PROD_GW, 25)
            restored["prod"] = True
        except Exception as exc:
            print(f"prod restore err {exc!r}", flush=True)
        try:
            if snap_gcp:
                restore_limits("gcp", snap_gcp)
            recreate("gcp", "live")
            wait_health(GCP_GW, 25)
            restored["gcp"] = True
        except Exception as exc:
            print(f"gcp restore err {exc!r}", flush=True)

    try:
        print("=== live probes (AWS ON, current config) ===", flush=True)
        probes["prod_live"] = run_probe("prod_live", extra_prod)
        probes["gcp_live"] = run_probe("gcp_live", extra_gcp)

        print("=== bypass recreate BOTH ===", flush=True)
        snap_prod = lift("prod", prod_key, True)
        snap_gcp = lift("gcp", gcp_key, True)
        recreate("prod", "bypass")
        recreate("gcp", "bypass")
        wait_health(PROD_GW)
        wait_health(GCP_GW)
        probes["prod_bypass"] = run_probe("prod_bypass", extra_prod)
        probes["gcp_bypass"] = run_probe("gcp_bypass", extra_gcp)

        print("=== HEALTH gcp then prod (full uncap) ===", flush=True)
        for inflight, w, c, dur in HEALTH_LEVELS:
            results.append(run_bench(f"gcp_H_if{inflight}", "gcp", "health", w, c, dur, extra_gcp))
        for inflight, w, c, dur in HEALTH_LEVELS:
            results.append(run_bench(f"prod_H_if{inflight}", "prod", "health", w, c, dur, extra_prod))

        print("=== CODE-ONLY chat gcp then prod ===", flush=True)
        for inflight, w, c, dur in CODE_LEVELS:
            r = run_bench(f"gcp_C_if{inflight}", "gcp", "chat", w, c, dur, extra_gcp)
            results.append(r)
            if (r.get("error_rate") or 0) >= 0.6:
                print("gcp C stop escalate", flush=True)
                break
        for inflight, w, c, dur in CODE_LEVELS:
            r = run_bench(f"prod_C_if{inflight}", "prod", "chat", w, c, dur, extra_prod)
            results.append(r)
            if (r.get("error_rate") or 0) >= 0.6:
                print("prod C stop escalate", flush=True)
                break
    finally:
        do_restore()

    probes["prod_after"] = run_probe("prod_after", extra_prod)
    table = [row(r) for r in results]
    def peak(prefix):
        rows = [r for r in results if str(r.get("_label","")).startswith(prefix) and r.get("ok_rps") is not None]
        return max(rows, key=lambda x: x.get("ok_rps") or 0) if rows else {}
    verdict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hosts": {
            "prod": "c8g.2xlarge 8 vCPU ~16GiB aarch64 Graviton, gateway mem cap 12GiB",
            "gcp": "16 vCPU ~58GiB x86_64 Xeon Platinum 8581C, gateway mem uncapped, nvme 512G",
        },
        "peak_health_gcp": peak("gcp_H_").get("ok_rps"),
        "peak_health_prod": peak("prod_H_").get("ok_rps"),
        "peak_code_gcp": peak("gcp_C_").get("ok_rps"),
        "peak_code_prod": peak("prod_C_").get("ok_rps"),
        "health_ratio_gcp_over_prod": None,
        "code_ratio_gcp_over_prod": None,
        "claimed_100k_live_nine_stage": False,
        "restored": restored,
        "table": table,
        "probes": {k: (v.get("records") or [{}])[0].get("stages") for k, v in probes.items()},
    }
    ph, pp = verdict["peak_health_gcp"], verdict["peak_health_prod"]
    ch, cp = verdict["peak_code_gcp"], verdict["peak_code_prod"]
    if ph and pp:
        verdict["health_ratio_gcp_over_prod"] = round(ph / pp, 2)
    if ch and cp:
        verdict["code_ratio_gcp_over_prod"] = round(ch / cp, 2)
    (OUTDIR / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: verdict[k] for k in (
        "peak_health_gcp","peak_health_prod","peak_code_gcp","peak_code_prod",
        "health_ratio_gcp_over_prod","code_ratio_gcp_over_prod","restored",
    )}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
