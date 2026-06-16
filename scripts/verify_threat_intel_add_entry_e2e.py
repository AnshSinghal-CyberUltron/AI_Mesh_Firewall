#!/usr/bin/env python3
"""E2E: M2.5 Add Entry → Sync → Gateway block → IOC Matches telemetry.

Usage:
  python scripts/verify_threat_intel_add_entry_e2e.py

Env overrides:
  CONTROL_URL, GATEWAY_URL, TEST_EMAIL, TEST_PASSWORD, ORG_SLUG, GATEWAY_KEY
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
ORG_SLUG = os.environ.get("ORG_SLUG", "zeroshield")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-4.5")
STAMP = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
INDICATOR = os.environ.get("IOC_INDICATOR", f"e2e_ioc_probe_{STAMP}")
THREAT_TYPE = os.environ.get("IOC_THREAT_TYPE", "e2e_jailbreak_probe")


def http_json(method: str, url: str, token: str | None = None, body: dict | None = None, api_key: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return e.code, payload


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"PASS: {msg}")


def main() -> None:
    print(f"=== Threat Intel Add Entry E2E (org={ORG_SLUG}) ===")
    print(f"Indicator: {INDICATOR}")

    code, token_payload = http_json(
        "POST",
        f"{CONTROL}/api/auth/token/",
        body={"email": EMAIL, "password": PASSWORD},
    )
    if code != 200 or "access" not in token_payload:
        fail(f"login failed ({code}): {token_payload}")
    token = token_payload["access"]
    ok(f"authenticated as {EMAIL}")

    code, baseline = http_json(
        "GET",
        f"{CONTROL}/api/module2/threat-intel/telemetry/?period=24h",
        token=token,
    )
    if code != 200:
        fail(f"baseline telemetry failed ({code}): {baseline}")
    base_hits = (baseline.get("summary") or {}).get("threat_intel_matches", 0)
    print(f"Baseline IOC Matches (24h): {base_hits}")

    entry_body = {
        "source": "manual",
        "threat_type": THREAT_TYPE,
        "indicator": INDICATOR,
        "owasp_code": "LLM01",
        "confidence": 0.9,
        "auto_block": True,
    }
    code, created = http_json(
        "POST",
        f"{CONTROL}/api/module2/threat-intel/",
        token=token,
        body=entry_body,
    )
    if code not in (200, 201) or "id" not in created:
        fail(f"create IOC failed ({code}): {created}")
    entry_id = created["id"]
    ok(f"Add Entry created id={entry_id}")

    code, listed = http_json("GET", f"{CONTROL}/api/module2/threat-intel/", token=token)
    if code != 200:
        fail(f"list IOC failed ({code}): {listed}")
    rows = listed if isinstance(listed, list) else listed.get("results", [])
    if not any(r.get("id") == entry_id for r in rows):
        fail("created entry not visible in list API")
    ok("entry visible in IOC list")

    code, sync_resp = http_json(
        "POST",
        f"{CONTROL}/api/module2/threat-intel/sync/",
        token=token,
    )
    if code not in (200, 202):
        fail(f"sync queue failed ({code}): {sync_resp}")
    ok(f"sync queued ({sync_resp.get('status', 'ok')}) entry_count={sync_resp.get('entry_count')}")

    # Celery workers are optional in dev — run sync task inline in control container.
    sync_cmd = [
        "docker",
        "exec",
        "ai_mesh_firewall-control-1",
        "python",
        "manage.py",
        "shell",
        "-c",
        (
            "from auth.models import Organization; "
            "from module2.tasks import sync_threat_intel_to_redis; "
            f"org=Organization.objects.filter(slug='{ORG_SLUG}').first(); "
            "assert org, 'org not found'; "
            "sync_threat_intel_to_redis(org.id); "
            "print('redis_sync_ok')"
        ),
    ]
    try:
        out = subprocess.check_output(sync_cmd, stderr=subprocess.STDOUT, text=True, timeout=90)
        if "redis_sync_ok" not in out:
            print(out)
            fail("inline redis sync did not complete")
        ok("Redis threat intel key updated (inline sync)")
    except subprocess.CalledProcessError as e:
        fail(f"inline redis sync failed: {e.output}")

    redis_key = f"firewall:threat_intel:{ORG_SLUG}"
    redis_cmd = ["docker", "exec", "ai_mesh_firewall-redis-1", "redis-cli", "GET", redis_key]
    try:
        redis_raw = subprocess.check_output(redis_cmd, text=True, timeout=30).strip()
        if INDICATOR not in redis_raw:
            fail(f"indicator not found in Redis key {redis_key}")
        ok(f"Redis contains indicator ({redis_key})")
    except subprocess.CalledProcessError as e:
        fail(f"redis read failed: {e}")

    gateway_key = os.environ.get("GATEWAY_KEY", "").strip()
    if not gateway_key:
        code, key_resp = http_json(
            "POST",
            f"{CONTROL}/api/gateways/keys/",
            token=token,
            body={"name": f"ti-e2e-{STAMP}", "project_id": "threat-intel-e2e"},
        )
        if code not in (200, 201) or not key_resp.get("key"):
            fail(f"could not create gateway key ({code}): {key_resp}")
        gateway_key = key_resp["key"]
    ok(f"gateway key prefix={gateway_key[:8]}...")

    prompt = f"Please process this user text: {INDICATOR}"
    gw_body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 16,
    }
    code, gw_resp = http_json(
        "POST",
        f"{GATEWAY}/v1/chat/completions",
        api_key=gateway_key,
        body=gw_body,
    )
    print(f"Gateway response ({code}): {json.dumps(gw_resp)[:300]}")
    if code not in (403, 503):
        fail(f"expected gateway block 403/503 for IOC match, got {code}")
    ok("gateway blocked prompt containing IOC indicator")

    # Ensure telemetry is drained (dev uses TELEMETRY_DRAIN_MODE=thread; nudge drain).
    drain_cmd = [
        "docker",
        "exec",
        "ai_mesh_firewall-control-1",
        "python",
        "manage.py",
        "shell",
        "-c",
        "from core.tasks import drain_telemetry_from_redis; print(drain_telemetry_from_redis(100))",
    ]
    try:
        subprocess.check_output(drain_cmd, stderr=subprocess.STDOUT, text=True, timeout=60)
    except subprocess.CalledProcessError:
        pass

    latest_hits = base_hits
    for attempt in range(12):
        time.sleep(2)
        code, telem = http_json(
            "GET",
            f"{CONTROL}/api/module2/threat-intel/telemetry/?period=24h&_={int(time.time())}",
            token=token,
        )
        if code != 200:
            continue
        latest_hits = (telem.get("summary") or {}).get("threat_intel_matches", 0)
        if latest_hits > base_hits:
            break
        print(f"  poll {attempt + 1}/12: IOC Matches still {latest_hits} (waiting…)")
    if latest_hits <= base_hits:
        fail(f"IOC Matches did not increase (before={base_hits}, after={latest_hits})")
    ok(f"IOC Matches increased to {latest_hits} (was {base_hits})")

    code, _ = http_json(
        "DELETE",
        f"{CONTROL}/api/module2/threat-intel/{entry_id}/",
        token=token,
    )
    if code not in (200, 204):
        fail(f"delete IOC failed ({code})")
    ok(f"cleanup deleted entry id={entry_id}")

    try:
        subprocess.check_output(sync_cmd, stderr=subprocess.STDOUT, text=True, timeout=90)
        ok("post-delete Redis sync completed")
    except subprocess.CalledProcessError:
        print("WARN: post-delete redis sync failed (non-fatal)")

    print("=== ALL E2E CHECKS PASSED ===")


if __name__ == "__main__":
    main()
