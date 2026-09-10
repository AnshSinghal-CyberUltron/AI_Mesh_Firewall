"""P0.0 fail-closed preflight. Helpers are unit-tested; main() talks to aimf_p0."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from p0_classify import TOKEN_FLOOR, build_chat_body, classify_sample

WORKTREE = Path("/home/contact_cyberultron_com/aimesh-p0-task0")
EVIDENCE = WORKTREE / "docs/perf/evidence/2026-09-10-p0-task0-honesty"
COMPOSE_FILES = [
    "-f",
    str(WORKTREE / "docker-compose.yml"),
    "-f",
    str(WORKTREE / "scripts/perf/e2e/compose.p0.yml"),
]
PROJECT = "aimf_p0"
ORG = "aimfp0"
FORBIDDEN_HOST_PORTS = frozenset(
    {
        ("127.0.0.1", 8300),
        ("localhost", 8300),
        ("127.0.0.1", 8100),
        ("localhost", 8100),
    }
)
EMPTY_PREFIXES = (
    "firewall:config",
    "policies:compiled",
    "llm:model_configs",
    "kill_switch:",
)


def refuse_gateway_url(url: str) -> None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    port = p.port or (443 if p.scheme == "https" else 80)
    if (host, port) in FORBIDDEN_HOST_PORTS:
        raise ValueError(f"forbidden GATEWAY_URL {url} (live aimeshperf / control)")


def peer_project_ok(labels: dict) -> bool:
    return (labels or {}).get("com.docker.compose.project") == PROJECT


def routing_catalog_ok(payload: dict | None) -> tuple[bool, str]:
    """Fail closed when Redis has no credentialed org inference model.

    Guard/internal rows are excluded from ``llm:model_configs`` already; an
    empty routing list is exactly the 422 ``no_provider_configured`` seed gap.
    """
    data = payload if isinstance(payload, dict) else {}
    routing = data.get("routing") or data.get("models") or []
    if not isinstance(routing, list) or not routing:
        return False, "Redis llm:model_configs routing is empty (no org inference model)"
    eligible = 0
    for entry in routing:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("provider") or "").strip().lower() == "internal":
            continue
        if entry.get("api_key_set") or str(entry.get("api_key_env_var") or "").strip():
            eligible += 1
    if eligible <= 0:
        return False, "no credentialed org inference model in Redis routing"
    return True, ""


def ram_matches_seed(
    redis_policy_count: int,
    observability: dict,
    expected_org: str,
) -> tuple[bool, str]:
    org = str((observability or {}).get("organization") or "")
    pol = (observability or {}).get("policy") or {}
    ram_count = int(pol.get("policy_count") or 0)
    if org != expected_org:
        return False, f"organization mismatch: {org!r} != {expected_org!r}"
    if redis_policy_count > 0 and ram_count <= 0:
        return False, "Redis has policies but RAM cache is empty (missed pub/sub or HMAC last-good)"
    if ram_count <= 0:
        return False, "in-process org policy_count is 0"
    return True, ""


def empty_redis_globs_ok(keys: list[str]) -> bool:
    for k in keys:
        for prefix in EMPTY_PREFIXES:
            if k == prefix or k.startswith(prefix):
                return False
    return True


def _compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "--project-name", PROJECT, *COMPOSE_FILES, *args]
    return subprocess.run(cmd, cwd=WORKTREE, check=check, capture_output=True, text=True)


def _inspect_labels(container: str) -> dict:
    raw = subprocess.check_output(
        ["docker", "inspect", "-f", "{{json .Config.Labels}}", container],
        text=True,
    )
    return json.loads(raw)


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=WORKTREE, text=True).strip()


def _http_json(url: str, *, headers: dict | None = None, data: bytes | None = None, timeout: float = 60.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        parsed = {"_raw": body[:400].decode("utf-8", "replace")}
    return status, parsed


def _redis_keys(match: str) -> list[str]:
    proc = _compose("exec", "-T", "redis", "redis-cli", "--scan", "--pattern", match, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


def _redis_get(key: str) -> str:
    proc = _compose("exec", "-T", "redis", "redis-cli", "GET", key, check=False)
    return (proc.stdout or "").strip()


def fingerprint_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def run_preflight(
    *,
    gateway_url: str,
    api_key: str,
    before_seed: bool = False,
) -> dict:
    refuse_gateway_url(gateway_url)
    report: dict[str, Any] = {"preflight": "fail", "gateway_url": gateway_url}
    sha = _git_sha()
    report["git_sha"] = sha

    gw_name = f"{PROJECT}-gateway-1"
    labels = _inspect_labels(gw_name)
    report["compose_project"] = labels.get("com.docker.compose.project")
    report["git_sha_label"] = labels.get("git.sha")
    if not peer_project_ok(labels):
        report["error"] = f"peer project {report['compose_project']!r} is not {PROJECT}"
        return report
    if labels.get("git.sha") != sha:
        report["error"] = f"git.sha label {labels.get('git.sha')!r} != HEAD {sha}"
        return report

    stale: list[str] = []
    for pat in ("firewall:config*", "policies:compiled*", "llm:model_configs*", "kill_switch:*"):
        stale.extend(_redis_keys(pat))
    report["redis_keys_seen"] = stale
    if before_seed and not empty_redis_globs_ok(stale):
        report["error"] = f"Redis not empty before seed: {stale[:20]}"
        return report

    if before_seed:
        report["preflight"] = "empty_ok"
        return report

    health_status, health = _http_json(gateway_url.rstrip("/") + "/health")
    report["health_status"] = health_status
    if health_status != 200:
        report["error"] = f"/health {health_status} {health}"
        return report

    compiled_keys = _redis_keys(f"policies:compiled:{ORG}")
    compiled_raw = _redis_get(f"policies:compiled:{ORG}") if compiled_keys else ""
    redis_count = 0
    compiled_meta: dict[str, Any] = {}
    if compiled_raw and compiled_raw != "(nil)":
        try:
            blob = json.loads(compiled_raw)
            redis_count = int(blob.get("policy_count") or len(blob.get("policies") or []))
            compiled_meta = {
                "policy_count": redis_count,
                "version": blob.get("version"),
                "compiled_at": blob.get("compiled_at"),
                "_sig": (blob.get("_sig") or "")[:16],
            }
        except json.JSONDecodeError:
            redis_count = 1
    report["redis_compiled"] = compiled_meta
    models_raw = _redis_get(f"llm:model_configs:{ORG}")
    models_blob: dict[str, Any] = {}
    if models_raw and models_raw != "(nil)":
        try:
            models_blob = json.loads(models_raw)
        except json.JSONDecodeError:
            models_blob = {}
    report["redis_routing_count"] = len(models_blob.get("routing") or [])
    ok_cat, cat_reason = routing_catalog_ok(models_blob)
    if not ok_cat:
        report["error"] = cat_reason
        return report
    ks = _redis_get(f"kill_switch:{ORG}:global")
    report["kill_switch"] = ks
    if ks and ks not in ("(nil)", "", "0", "false", "False"):
        report["error"] = f"kill-switch armed: {ks!r}"
        return report

    cfg_raw = _redis_get(f"firewall:config:{ORG}")
    report["redis_config_present"] = bool(cfg_raw) and cfg_raw != "(nil)"

    obs_status, obs = _http_json(
        gateway_url.rstrip("/") + "/v1/observability",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    report["observability_status"] = obs_status
    report["observability"] = {
        "organization": (obs or {}).get("organization"),
        "policy": (obs or {}).get("policy"),
    }
    ok, reason = ram_matches_seed(redis_count, obs if isinstance(obs, dict) else {}, ORG)
    if not ok:
        report["error"] = reason
        return report

    t2 = _compose("exec", "-T", "gateway", "printenv", "ENABLE_TIER2").stdout.strip()
    dur = _compose("exec", "-T", "gateway", "printenv", "GATEWAY_LOADTEST_STUB_DURATION_S").stdout.strip()
    wc = _compose("exec", "-T", "gateway", "printenv", "WEB_CONCURRENCY").stdout.strip()
    ground = _compose("exec", "-T", "gateway", "printenv", "GATEWAY_OUTPUT_GROUNDING_ENABLED").stdout.strip()
    report["pins"] = {"ENABLE_TIER2": t2, "DURATION": dur, "WEB_CONCURRENCY": wc, "GROUNDING": ground}
    if t2.lower() not in ("false", "0"):
        report["error"] = f"ENABLE_TIER2={t2!r}"
        return report
    if float(dur or "0") <= 0:
        report["error"] = f"stub duration {dur!r}"
        return report
    if wc != "1":
        report["error"] = f"WEB_CONCURRENCY={wc!r}"
        return report
    if ground.lower() not in ("false", "0"):
        report["error"] = f"GROUNDING={ground!r}"
        return report
    gw_key = _compose("exec", "-T", "gateway", "printenv", "POLICY_SIGNING_KEY").stdout.strip()
    ctrl_key = _compose("exec", "-T", "control", "printenv", "POLICY_SIGNING_KEY").stdout.strip()
    report["signing_fp"] = {"gateway": fingerprint_secret(gw_key), "control": fingerprint_secret(ctrl_key)}
    if not gw_key or gw_key != ctrl_key:
        report["error"] = "POLICY_SIGNING_KEY mismatch or empty"
        return report

    nonce = str(uuid.uuid4())
    payload = json.dumps(build_chat_body(nonce)).encode("utf-8")
    status, body = _http_json(
        gateway_url.rstrip("/") + "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=payload,
        timeout=120.0,
    )
    cls = classify_sample(status, body, stream=False)
    err = body.get("error") if isinstance(body, dict) else None
    err_code = None
    if isinstance(err, dict):
        err_code = err.get("code") or err.get("error")
    elif isinstance(err, str):
        err_code = err
    report["probe"] = {
        "status": status,
        "class": cls.class_name,
        "counted": cls.counted,
        "completion_tokens": (body.get("usage") or {}).get("completion_tokens") if isinstance(body, dict) else None,
        "error_code": err_code,
        "blocked_by": body.get("blocked_by") if isinstance(body, dict) else None,
    }
    if not cls.counted:
        report["error"] = f"probe not Counted_Sample: {cls.class_name} status={status} code={err_code}"
        report["probe_body_keys"] = list(body) if isinstance(body, dict) else type(body).__name__
        return report

    report["preflight"] = "pass"
    report["token_floor"] = TOKEN_FLOOR
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    gateway_url = os.environ.get("GATEWAY_URL", "http://127.0.0.1:18300")
    api_key = os.environ.get("P0_API_KEY", "")
    before_seed = "--before-seed" in argv
    post_run = "--post-run" in argv
    if not before_seed and not api_key:
        print("P0_API_KEY required", file=sys.stderr)
        return 2
    try:
        refuse_gateway_url(gateway_url)
        report = run_preflight(gateway_url=gateway_url, api_key=api_key, before_seed=before_seed)
    except Exception as exc:
        report = {"preflight": "fail", "error": str(exc), "gateway_url": gateway_url}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if before_seed:
        out = EVIDENCE / "preflight_empty.json"
    elif post_run:
        out = EVIDENCE / "postflight.json"
    else:
        out = EVIDENCE / "preflight.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("preflight", "error") if k in report} | {"wrote": str(out)}))
    return 0 if report.get("preflight") in ("pass", "empty_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
