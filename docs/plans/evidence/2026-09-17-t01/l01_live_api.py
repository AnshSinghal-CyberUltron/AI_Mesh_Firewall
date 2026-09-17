#!/usr/bin/env python3
"""T01 live L01-1/2/3 API+Redis+gateway proofs. Never prints secrets."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

KEYS = Path("/tmp/v3a02.keys.json")
EVIDENCE = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs/plans/evidence/2026-09-17-t01")
SNAPSHOT = EVIDENCE / "synth-snapshot-before.json"
CONTROL = "http://127.0.0.1:8100"
GATEWAY = "http://127.0.0.1:8300"
REDIS = "ai_mesh_firewall-redis-1"
SSN = "123-45-6789"


def _load_keys():
    return json.loads(KEYS.read_text())


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
            parsed = {"_raw": raw[:500].decode("utf-8", "replace")}
        return exc.code, parsed, dict(exc.headers)


def redis_config(slug: str) -> dict | None:
    out = subprocess.check_output(
        ["docker", "exec", REDIS, "redis-cli", "GET", f"firewall:config:{slug}"],
        text=True,
    ).strip()
    if not out or out == "(nil)":
        return None
    return json.loads(out)


def restore_snapshot(token: str, snapshot: dict, slug: str) -> None:
    row = dict(snapshot[slug])
    row.pop("org_id", None)
    row.pop("org_slug", None)
    status, body, _ = _http("PUT", f"{CONTROL}/api/firewall/config/", token=token, body=row)
    if status != 200:
        raise SystemExit(f"restore {slug} failed {status} {body}")


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


def main() -> None:
    keys = _load_keys()
    snapshot = json.loads(SNAPSHOT.read_text())
    email = keys["frontend"]["email"]
    password = keys["frontend"]["password"]
    block_key = keys["orgs"]["v3a02-block"]["raw"]
    observe_key = keys["orgs"]["v3a02-observe"]["raw"]

    st, tok, _ = _http(
        "POST",
        f"{CONTROL}/api/auth/token/",
        body={"email": email, "password": password},
    )
    if st != 200:
        raise SystemExit(f"login failed {st} {tok}")
    token = tok["access"]

    evidence = {
        "task": "T01",
        "gates": {},
        "notes": [],
        "do_not_claim": ["20ms measured", "1064 RPS measured"],
    }

    try:
        # --- L01-1 save/reload/runtime map ---
        st, before, _ = _http("GET", f"{CONTROL}/api/firewall/config/", token=token)
        l01_1 = {
            "get_status": st,
            "stored_enforcement_before": (before or {}).get("enforcement_mode"),
            "stored_pii_before": (before or {}).get("pii_detection_enabled"),
        }
        st, put_mon, hdrs = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"enforcement_mode": "monitor"},
        )
        time.sleep(1.5)
        redis_mon = redis_config("v3a02-block")
        st2, after, _ = _http("GET", f"{CONTROL}/api/firewall/config/", token=token)
        st3, put_block, _ = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"enforcement_mode": "block"},
        )
        time.sleep(1.0)
        redis_block = redis_config("v3a02-block")
        l01_1.update(
            {
                "put_monitor_status": st,
                "put_monitor_x_request_id": hdrs.get("X-Request-Id") or hdrs.get("x-request-id"),
                "get_after_monitor": (after or {}).get("enforcement_mode"),
                "redis_after_monitor": (redis_mon or {}).get("enforcement_mode"),
                "redis_has_pii_key_after_monitor": "pii_detection_enabled" in (redis_mon or {}),
                "put_block_status": st3,
                "redis_after_block": (redis_block or {}).get("enforcement_mode"),
                "pass": (
                    st == 200
                    and (after or {}).get("enforcement_mode") == "monitor"
                    and (redis_mon or {}).get("enforcement_mode") == "monitor"
                    and st3 == 200
                    and (redis_block or {}).get("enforcement_mode") == "block"
                ),
            }
        )
        evidence["gates"]["L01-1"] = l01_1

        # --- L01-2 rewrite reject ---
        st, rewrite_body, _ = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"output_pii_action": "rewrite"},
        )
        cfg_after_reject = redis_config("v3a02-block")
        st_md, md_body, _ = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"output_pii_action": "redact"},
        )
        # Rule write
        st_pol, policies, _ = _http(
            "GET", f"{CONTROL}/api/policies/?policy_domain=mcp", token=token
        )
        policy_id = None
        if isinstance(policies, list) and policies:
            policy_id = policies[0].get("id")
        elif isinstance(policies, dict):
            results = policies.get("results") or []
            if results:
                policy_id = results[0].get("id")
        rule_status, rule_body = None, None
        if policy_id:
            rule_status, rule_body, _ = _http(
                "POST",
                f"{CONTROL}/api/policies/{policy_id}/rules/",
                token=token,
                body={
                    "name": "t01-rewrite-probe",
                    "rule_type": "keywords",
                    "condition": {"keywords": ["T01_REWRITE_PROBE"], "field": "both"},
                    "action": "rewrite",
                    "priority": 1,
                    "enabled": True,
                },
            )
        l01_2 = {
            "put_rewrite_status": st,
            "put_rewrite_error_has_rewrite": "rewrite" in json.dumps(rewrite_body).lower(),
            "redis_output_pii_action_unchanged_not_rewrite": (cfg_after_reject or {}).get("output_pii_action")
            != "rewrite",
            "put_redact_status": st_md,
            "rule_post_status": rule_status,
            "rule_post_body_keys": list(rule_body) if isinstance(rule_body, dict) else None,
            "pass": bool(st == 400 and rule_status == 400),
        }
        evidence["gates"]["L01-2"] = l01_2

        # --- L01-3 PII off ---
        def scan_only(prompt: str):
            return _http(
                "POST",
                f"{GATEWAY}/v1/chat/completions",
                api_key=block_key,
                body={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 0,
                    "stream": False,
                },
                timeout=90,
            )

        prompt = f"Please say hello. Reference number {SSN}."
        # A/B: floor ON then OFF (scan-only so synth org without BYOK still runs input_scan).
        st_on, _, _ = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"pii_detection_enabled": True},
        )
        time.sleep(2.0)
        http_on, chat_on, hdr_on = scan_only(prompt)
        trace_on = pipeline_trace(chat_on) or (chat_on.get("pipeline_trace") if isinstance(chat_on, dict) else {}) or {}
        stages_on = {s.get("name"): s for s in (trace_on.get("stages") or []) if isinstance(s, dict)}
        zs_on = (chat_on or {}).get("zeroshield") or {}

        st, pii_put, _ = _http(
            "PUT",
            f"{CONTROL}/api/firewall/config/",
            token=token,
            body={"pii_detection_enabled": False},
        )
        time.sleep(2.0)
        redis_pii = redis_config("v3a02-block")
        http_off, chat_off, hdr_off = scan_only(prompt)
        trace_off = pipeline_trace(chat_off) or (chat_off.get("pipeline_trace") if isinstance(chat_off, dict) else {}) or {}
        stages_off = {s.get("name"): s for s in (trace_off.get("stages") or []) if isinstance(s, dict)}
        zs_off = (chat_off or {}).get("zeroshield") or {}
        input_on = (stages_on.get("input_scan") or {}).get("action")
        input_off = (stages_off.get("input_scan") or {}).get("action")
        threat_off = (zs_off.get("threat_type") or "none") or "none"

        # AKIA is both PII-inventory and secret; secret floor wins when PII OFF.
        http_akia, chat_akia, hdr_akia = scan_only("Please use key AKIAIOSFODNN7EXAMPLE")
        trace_akia = pipeline_trace(chat_akia) if isinstance(chat_akia, dict) else {}
        if not trace_akia and isinstance(chat_akia, dict):
            trace_akia = pipeline_trace(chat_akia)
        stages_akia = {s.get("name"): s for s in (trace_akia.get("stages") or []) if isinstance(s, dict)}
        zs_akia = (chat_akia or {}).get("zeroshield") or {}

        # Secrets floor still runs when PII is off.
        pat = "ghp_" + ("A" * 36)
        http_pat, chat_pat, hdr_pat = scan_only(f"token {pat}")
        trace_pat = pipeline_trace(chat_pat) if isinstance(chat_pat, dict) else {}
        stages_pat = {s.get("name"): s for s in (trace_pat.get("stages") or []) if isinstance(s, dict)}
        zs_pat = (chat_pat or {}).get("zeroshield") or {}

        st_x, xbody, _ = _http(
            "GET",
            f"{CONTROL}/api/firewall/config/",
            token=token,
        )
        jwt_org = (xbody or {}).get("organization") or (xbody or {}).get("org_slug")
        # Integrity: content toggle cannot disable the service switch.
        firewall_still_on = (xbody or {}).get("firewall_enabled") is True

        l01_3 = {
            "put_pii_true_status": st_on,
            "put_pii_false_status": st,
            "redis_pii_detection_enabled": (redis_pii or {}).get("pii_detection_enabled"),
            "redis_has_key": "pii_detection_enabled" in (redis_pii or {}),
            "scan_only": True,
            "pii_on": {
                "http": http_on,
                "zs_action": zs_on.get("action"),
                "threat": zs_on.get("threat_type"),
                "input_scan": input_on,
                "final": trace_on.get("final_action"),
                "x_request_id": hdr_on.get("x-request-id") or hdr_on.get("X-Request-Id"),
            },
            "pii_off": {
                "http": http_off,
                "zs_action": zs_off.get("action"),
                "threat": zs_off.get("threat_type"),
                "input_scan": input_off,
                "final": trace_off.get("final_action"),
                "x_request_id": hdr_off.get("x-request-id") or hdr_off.get("X-Request-Id"),
            },
            "akia_pii_off": {
                "http": http_akia,
                "zs_action": zs_akia.get("action"),
                "threat": zs_akia.get("threat_type"),
                "input_scan": (stages_akia.get("input_scan") or {}).get("action"),
                "final": trace_akia.get("final_action"),
                "x_request_id": hdr_akia.get("x-request-id") or hdr_akia.get("X-Request-Id"),
            },
            "github_pat_pii_off": {
                "http": http_pat,
                "zs_action": zs_pat.get("action"),
                "threat": zs_pat.get("threat_type"),
                "input_scan": (stages_pat.get("input_scan") or {}).get("action"),
                "final": trace_pat.get("final_action"),
                "x_request_id": hdr_pat.get("x-request-id") or hdr_pat.get("X-Request-Id"),
            },
            "jwt_config_org": jwt_org,
            "firewall_enabled_stays_true": firewall_still_on,
            "pass": (
                st_on == 200
                and st == 200
                and http_on == 200
                and http_off == 200
                and (redis_pii or {}).get("pii_detection_enabled") is False
                and input_on == "redact"
                and input_off == "allow"
                and str(threat_off).lower() in ("none", "", "clean")
                and (zs_akia.get("threat_type") == "secret"
                     or (stages_akia.get("input_scan") or {}).get("action") in ("redact", "block"))
                and (zs_akia.get("action") in ("redact", "block") or http_akia in (400, 403))
                and firewall_still_on
            ),
        }
        evidence["gates"]["L01-3"] = l01_3
        evidence["l01_3_trace_keys"] = list(trace_off)[:20]
    finally:
        restore_snapshot(token, snapshot, "v3a02-block")
        time.sleep(0.5)
        redis_restored = redis_config("v3a02-block")
        evidence["rollback"] = {
            "v3a02-block_enforcement": (redis_restored or {}).get("enforcement_mode"),
            "v3a02-block_pii": (redis_restored or {}).get("pii_detection_enabled"),
            "matches_snapshot_mode": (redis_restored or {}).get("enforcement_mode")
            == snapshot["v3a02-block"]["enforcement_mode"],
        }

    evidence["overall_l01"] = all(g.get("pass") for g in evidence["gates"].values())
    dest = EVIDENCE / "l01_live_api.json"
    dest.write_text(json.dumps(evidence, indent=2, default=str))
    print(json.dumps(evidence, indent=2, default=str))


if __name__ == "__main__":
    main()
