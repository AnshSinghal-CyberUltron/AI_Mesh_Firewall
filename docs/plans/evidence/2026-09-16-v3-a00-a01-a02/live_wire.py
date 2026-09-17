#!/usr/bin/env python3
"""V3 A02 live wire proof. Never prints API key bodies. Writes sanitized JSON."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

OUT = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/plans/evidence/2026-09-16-v3-a00-a01-a02/A02/live_wire.json")
GW = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100")
PII = "Please process SSN 123-45-6789 and email john.doe@example.com for payroll."
CLEAN = "What is 2+2? Reply with one sentence."
SSN = "123-45-6789"
EMAIL = "john.doe@example.com"


def sh(cmd: list[str], timeout: int = 60) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:] or p.stdout[-2000:])
    return p.stdout


def load_keys() -> dict:
    raw = sh(["docker", "exec", "ai_mesh_firewall-control-1", "cat", "/tmp/v3a02.keys.json"])
    return json.loads(raw)


def http_json(method: str, url: str, *, headers: dict | None = None, body: dict | None = None, timeout: int = 60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        if v:
            req.add_header(k, v)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            parsed = json.loads(raw.decode("utf-8") or "null")
            rid = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
            return {
                "http_status": resp.status,
                "ms": round(ms, 1),
                "request_id": rid,
                "body": parsed,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        ms = (time.perf_counter() - t0) * 1000
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except Exception:
            parsed = {"_non_json": raw[:400].decode("utf-8", "replace")}
        rid = exc.headers.get("X-Request-ID") if exc.headers else None
        return {
            "http_status": exc.code,
            "ms": round(ms, 1),
            "request_id": rid,
            "body": parsed,
        }


def sanitize_body(body):
    if not isinstance(body, dict):
        text = json.dumps(body, default=str)
        return {
            "leaked_ssn": SSN in text,
            "leaked_email": EMAIL in text,
            "keys": [],
        }
    text = json.dumps(body, default=str)
    trace = body.get("pipeline_trace") if isinstance(body.get("pipeline_trace"), dict) else {}
    stages = []
    for st in trace.get("stages") or []:
        if not isinstance(st, dict):
            continue
        stages.append(
            {
                "name": st.get("name") or st.get("stage"),
                "action": st.get("action"),
                "latency_ms": st.get("latency_ms"),
            }
        )
    zs = body.get("zeroshield") if isinstance(body.get("zeroshield"), dict) else {}
    err = body.get("error")
    return {
        "error": err if isinstance(err, str) else (err or {}).get("message") if isinstance(err, dict) else err,
        "code": body.get("code") or (err.get("code") if isinstance(err, dict) else None),
        "blocked_by": body.get("blocked_by"),
        "message": (body.get("message") or "")[:240],
        "zeroshield_action": zs.get("action"),
        "final_action": trace.get("final_action"),
        "stages": stages,
        "leaked_ssn": SSN in text,
        "leaked_email": EMAIL in text,
        "has_pipeline_trace": bool(trace),
        "prompt_submitted_has_ssn": SSN in str(trace.get("prompt_submitted") or ""),
    }


def redis_cfg(slug: str) -> dict:
    raw = sh(["docker", "exec", "ai_mesh_firewall-redis-1", "redis-cli", "GET", f"firewall:config:{slug}"])
    raw = raw.strip()
    if not raw or raw == "(nil)":
        return {"present": False}
    cfg = json.loads(raw)
    return {
        "present": True,
        "enforcement_mode": cfg.get("enforcement_mode"),
        "firewall_enabled": cfg.get("firewall_enabled"),
        "has_pii_detection_enabled": "pii_detection_enabled" in cfg,
        "pii_detection_enabled": cfg.get("pii_detection_enabled"),
        "output_pii_enabled": cfg.get("output_pii_enabled"),
        "output_pii_action": cfg.get("output_pii_action"),
        "payload_keys": sorted(cfg.keys()),
    }


def event_for(request_id: str | None) -> dict | None:
    if not request_id:
        return None
    py = f"""
import json
from policy.models import EnforcementEvent
qs = EnforcementEvent.objects.filter(request_id='{request_id}').order_by('id')
rows=[]
for e in qs[:8]:
    meta=e.metadata if isinstance(e.metadata, dict) else {{}}
    rows.append({{
        'id': e.id,
        'action': getattr(e,'action',None),
        'event_type': getattr(e,'event_type',None),
        'threat_type': getattr(e,'threat_type',None) or meta.get('threat_type'),
        'http_status': getattr(e,'status_code',None) or meta.get('status_code'),
        'org_id': getattr(e,'organization_id',None),
        'has_pipeline_trace': isinstance(meta.get('pipeline_trace'), dict),
        'meta_keys': sorted(list(meta.keys()))[:24],
    }})
print(json.dumps(rows))
"""
    try:
        out = sh(
            ["docker", "exec", "ai_mesh_firewall-control-1", "python", "manage.py", "shell", "-c", py],
            timeout=40,
        )
        line = [ln for ln in out.splitlines() if ln.startswith("[")][-1]
        return json.loads(line)
    except Exception as exc:
        return {"error": str(exc)[:300]}


def chat(key: str, prompt: str, model: str) -> dict:
    res = http_json(
        "POST",
        f"{GW}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 64,
            "stream": False,
        },
        timeout=90,
    )
    body = res["body"] if isinstance(res["body"], dict) else {}
    rid = res.get("request_id") or (body.get("id") if isinstance(body, dict) else None)
    if not rid and isinstance(body, dict):
        rid = (body.get("pipeline_trace") or {}).get("request_id")
    return {
        **{k: res[k] for k in ("http_status", "ms", "request_id")},
        "sanitized": sanitize_body(body),
        "events": event_for(rid),
    }


def policy_check(key: str, prompt: str) -> dict:
    res = http_json(
        "POST",
        f"{GW}/v1/policy/check",
        headers={"Authorization": f"Bearer {key}"},
        body={"prompt": prompt},
        timeout=60,
    )
    body = res["body"] if isinstance(res["body"], dict) else {}
    return {
        **{k: res[k] for k in ("http_status", "ms", "request_id")},
        "sanitized": sanitize_body(body),
    }


def main() -> None:
    keys = load_keys()
    block_raw = keys["orgs"]["v3a02-block"]["raw"]
    observe_raw = keys["orgs"]["v3a02-observe"]["raw"]
    report = {
        "gateway": GW,
        "redis": {
            "v3a02-block": redis_cfg("v3a02-block"),
            "v3a02-observe": redis_cfg("v3a02-observe"),
            "zeroshield": redis_cfg("zeroshield"),
        },
        "chat": {},
        "policy_check": {},
        "db_pii_flags": {},
    }
    py = """
import json
from core.models import FirewallConfig
from auth.models import Organization
out={}
for slug in ['v3a02-block','v3a02-observe','zeroshield']:
    org=Organization.objects.get(slug=slug)
    cfg=FirewallConfig.load(organization=org)
    out[slug]={'pii_detection_enabled': cfg.pii_detection_enabled, 'enforcement_mode': cfg.enforcement_mode, 'firewall_enabled': cfg.firewall_enabled, 'output_pii_enabled': cfg.output_pii_enabled, 'output_pii_action': cfg.output_pii_action}
print(json.dumps(out))
"""
    db_out = sh(["docker", "exec", "ai_mesh_firewall-control-1", "python", "manage.py", "shell", "-c", py])
    report["db_pii_flags"] = json.loads([ln for ln in db_out.splitlines() if ln.startswith("{")][-1])

    for label, raw in (("block", block_raw), ("observe", observe_raw)):
        report["chat"][f"{label}-pii-gpt4omini"] = chat(raw, PII, "gpt-4o-mini")
        report["chat"][f"{label}-pii-auto"] = chat(raw, PII, "auto")
        report["chat"][f"{label}-clean-gpt4omini"] = chat(raw, CLEAN, "gpt-4o-mini")
        report["policy_check"][f"{label}-pii"] = policy_check(raw, PII)
        report["policy_check"][f"{label}-clean"] = policy_check(raw, CLEAN)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2)[:200000])
    print(json.dumps({"wrote": str(OUT), "summary": {
        "redis_block_mode": report["redis"]["v3a02-block"].get("enforcement_mode"),
        "redis_observe_mode": report["redis"]["v3a02-observe"].get("enforcement_mode"),
        "redis_has_pii_key_block": report["redis"]["v3a02-block"].get("has_pii_detection_enabled"),
        "chat_block_pii_status": report["chat"]["block-pii-gpt4omini"]["http_status"],
        "chat_block_pii_code": report["chat"]["block-pii-gpt4omini"]["sanitized"].get("code"),
        "chat_block_leaked_ssn": report["chat"]["block-pii-gpt4omini"]["sanitized"].get("leaked_ssn"),
        "chat_observe_pii_status": report["chat"]["observe-pii-gpt4omini"]["http_status"],
        "chat_observe_leaked_ssn": report["chat"]["observe-pii-gpt4omini"]["sanitized"].get("leaked_ssn"),
        "trace_block": report["chat"]["block-pii-gpt4omini"]["sanitized"].get("has_pipeline_trace"),
        "final_action_block": report["chat"]["block-pii-gpt4omini"]["sanitized"].get("final_action"),
    }}, indent=2))


if __name__ == "__main__":
    main()
