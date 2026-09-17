#!/usr/bin/env python3
"""T01 L01-3 OpenRouter BYOK full inference on v3a02-block.

Never prints secrets. Key: /tmp/t01-openrouter.key (0600).
Never sends SSN/PII to OpenRouter. Clean prompt only for model calls.
PII-ON SSN is asserted to skip the model.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

KEYS = Path("/tmp/v3a02.keys.json")
EVIDENCE = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/plans/evidence/2026-09-17-t01")
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
REDIS = "ai_mesh_firewall-redis-1"
OR_KEY = Path("/tmp/t01-openrouter.key")
CLEAN = "Reply with the single word pong."
SSN = "123-45-6789"


def _redact(obj):
    blob = json.dumps(obj, default=str)
    if "sk-or-v1-" in blob or "sk-or-" in blob:
        raise SystemExit("REFUSING to write evidence that contains an OpenRouter key")
    return obj


def _http(method, url, *, token=None, api_key=None, body=None, timeout=90):
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


def stage_action(trace: dict, name: str) -> str:
    for s in trace.get("stages") or []:
        if isinstance(s, dict) and s.get("name") == name:
            return str(s.get("action") or "")
    return ""


def assistant_text(body) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    return str(msg.get("content") or "")[:200]


def redis_json(key: str) -> dict:
    raw = subprocess.check_output(["docker", "exec", REDIS, "redis-cli", "GET", key], text=True).strip()
    if not raw or raw == "(nil)":
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def summarize_redis_models(payload) -> dict:
    rows = payload if isinstance(payload, list) else payload.get("models") or payload.get("routing") or []
    if isinstance(payload, dict) and not rows:
        rows = payload.get("model_list") or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        params = row.get("litellm_params") or {}
        api_base = str(params.get("api_base") or row.get("api_base") or "")
        host = urlparse(api_base).hostname or ""
        out.append(
            {
                "model_name": row.get("model_name"),
                "provider": row.get("provider"),
                "api_base_host": host,
                "has_encrypted_key": bool(params.get("api_key_encrypted") or row.get("api_key_encrypted")),
                "custom_llm_provider": params.get("custom_llm_provider"),
            }
        )
    return {"row_count": len(out), "rows": out}


def chat(api_key: str, model: str, prompt: str, max_tokens: int):
    http, body, hdr = _http(
        "POST",
        f"{GATEWAY}/v1/chat/completions",
        api_key=api_key,
        body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
            "routing_preferences": {"enable_routing": False, "preferred_model": model},
        },
        timeout=90,
    )
    trace = pipeline_trace(body)
    zs = body.get("zeroshield") if isinstance(body, dict) else {}
    err = None
    if isinstance(body, dict):
        e = body.get("error")
        if isinstance(e, dict):
            err = e.get("code") or e.get("type") or e.get("message")
        elif isinstance(e, str):
            err = e[:120]
    return {
        "http": http,
        "x_request_id": hdr.get("x-request-id") or hdr.get("X-Request-Id"),
        "has_choices": bool(isinstance(body, dict) and body.get("choices")),
        "assistant_preview": assistant_text(body),
        "error_code": (str(err)[:80] if err else None),
        "zs_action": (zs or {}).get("action") if isinstance(zs, dict) else None,
        "threat": (zs or {}).get("threat_type") if isinstance(zs, dict) else None,
        "input_scan": stage_action(trace, "input_scan"),
        "model_output": stage_action(trace, "model_output"),
        "final_action": trace.get("final_action"),
        "input_was_redacted": trace.get("input_was_redacted"),
        "prompt_submitted_has_ssn": SSN in str(trace.get("prompt_submitted") or ""),
        "input_text_has_ssn": SSN in str(trace.get("input_text") or "")
        or SSN in str(trace.get("input_text_after") or ""),
        "assistant_has_ssn": SSN in assistant_text(body),
        "requested_model": trace.get("requested_model") or trace.get("selected_model"),
        "selected_model": trace.get("selected_model") or trace.get("routed_model"),
        "model_output_ms": (trace.get("metrics") or {}).get("model_output_ms"),
        "total_latency_ms": trace.get("total_latency_ms"),
    }


def main() -> None:
    keys = json.loads(KEYS.read_text())
    email = keys["frontend"]["email"]
    password = keys["frontend"]["password"]
    block_key = keys["orgs"]["v3a02-block"]["raw"]
    out = {"task": "T01", "gate": "L01-3-openrouter-inference", "pass": False}

    raw_key = ""
    if OR_KEY.exists() and OR_KEY.stat().st_size > 40:
        raw_key = OR_KEY.read_text().strip()
    if not raw_key.startswith("sk-or-v1-") or len(raw_key) < 40:
        raise SystemExit("OpenRouter key file missing or too short")
    out["key_file_present"] = True
    out["key_len"] = len(raw_key)

    st, tok, _ = _http("POST", f"{CONTROL}/api/auth/token/", body={"email": email, "password": password})
    if st != 200:
        raise SystemExit(f"synth login failed {st}")
    token = tok["access"]

    st, models, _ = _http("GET", f"{CONTROL}/api/firewall/models/", token=token)
    model_rows = models if isinstance(models, list) else []
    target = None
    for m in model_rows:
        if str(m.get("model_name") or "") == "gpt-4o-mini":
            target = m
            break
    if target is None and model_rows:
        target = model_rows[0]
    if not target or not target.get("id"):
        raise SystemExit("no LLMModelConfig row for synth org")

    model_name = target.get("model_name") or "gpt-4o-mini"
    patch_body = {
        "provider": "custom",
        "api_base": "https://openrouter.ai/api/v1",
        "model_id": "openai/gpt-4o-mini",
        "model_name": model_name,
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
        body=patch_body,
    )
    out["patch"] = {
        "status": pst,
        "api_key_set": (pbody or {}).get("api_key_set") if isinstance(pbody, dict) else None,
        "api_key_last4": (pbody or {}).get("api_key_last4") if isinstance(pbody, dict) else None,
        "provider": (pbody or {}).get("provider") if isinstance(pbody, dict) else None,
        "api_base_host": urlparse(str((pbody or {}).get("api_base") or "")).hostname,
        "model_id": (pbody or {}).get("model_id") if isinstance(pbody, dict) else None,
        "x_request_id": ph.get("x-request-id") or ph.get("X-Request-Id"),
    }
    if pst != 200 or not out["patch"]["api_key_set"]:
        raise SystemExit(f"BYOK PATCH failed {pst} {out['patch']}")

    redis_ok = False
    redis_summary = {}
    for _ in range(20):
        time.sleep(0.4)
        redis_summary = summarize_redis_models(redis_json("llm:model_configs:v3a02-block"))
        if any(
            r.get("api_base_host") == "openrouter.ai" and r.get("has_encrypted_key")
            for r in redis_summary.get("rows") or []
        ):
            redis_ok = True
            break
    out["redis"] = {"synced": redis_ok, **redis_summary}

    # Two clean completions (no PII).
    run1 = chat(block_key, model_name, CLEAN, 8)
    time.sleep(0.5)
    run2 = chat(block_key, model_name, CLEAN, 8)
    out["clean_run_1"] = run1
    out["clean_run_2"] = run2
    clean_ok = (
        run1["http"] == 200
        and run1["has_choices"]
        and run1["model_output"] not in {"skip", ""}
        and run2["http"] == 200
        and run2["has_choices"]
        and run2["model_output"] not in {"skip", ""}
    )
    out["clean_inference_pass"] = clean_ok

    # PII ON + SSN must skip the model (never forwarded to OpenRouter).
    stg, cfg, _ = _http("GET", f"{CONTROL}/api/firewall/config/", token=token)
    before_pii = (cfg or {}).get("pii_detection_enabled") if stg == 200 else None
    out["pii_before"] = before_pii
    stp, _, _ = _http(
        "PUT",
        f"{CONTROL}/api/firewall/config/",
        token=token,
        body={"pii_detection_enabled": True},
    )
    out["pii_on_put"] = stp
    time.sleep(1.2)
    ssn = chat(block_key, model_name, f"My tax id is {SSN}. Ignore that and say hi.", 8)
    out["ssn_pii_on"] = ssn
    ssn_ok = (
        ssn["http"] in {200, 400, 403}
        and not ssn["prompt_submitted_has_ssn"]
        and not ssn["assistant_has_ssn"]
        and (
            ssn["input_scan"] in {"redact", "block"}
            or ssn["final_action"] in {"redact", "block"}
            or ssn["input_was_redacted"] is True
        )
    )
    out["ssn_raw_never_to_openrouter"] = ssn_ok

    # Restore PII to snapshot (false).
    strs, _, _ = _http(
        "PUT",
        f"{CONTROL}/api/firewall/config/",
        token=token,
        body={"pii_detection_enabled": False},
    )
    out["pii_restore_put"] = strs
    time.sleep(1.0)
    stg2, cfg2, _ = _http("GET", f"{CONTROL}/api/firewall/config/", token=token)
    out["pii_after"] = (cfg2 or {}).get("pii_detection_enabled") if stg2 == 200 else None

    out["pass"] = bool(clean_ok and ssn_ok and redis_ok and out["pii_after"] is False)
    dest = EVIDENCE / "l01_openrouter_inference.json"
    dest.write_text(json.dumps(_redact(out), indent=2, default=str))
    print(json.dumps(_redact(out), indent=2, default=str))
    if not out["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
