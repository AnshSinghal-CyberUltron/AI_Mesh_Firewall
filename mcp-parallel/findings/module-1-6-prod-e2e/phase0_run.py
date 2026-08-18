#!/usr/bin/env python3
"""Module 1.6 Phase0 prod E2E — OpenAI SDK + control APIs. Writes evidence JSON."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from openai import OpenAI, APIStatusError

OUT = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/mcp-parallel/findings/module-1-6-prod-e2e")
OUT.mkdir(parents=True, exist_ok=True)
API_KEY = Path("/tmp/prod_sdk_key.txt").read_text().strip()
GW = "https://aimeshgateway.zeroshield.ai/v1"
CTRL = "https://aimeshbackend.zeroshield.ai"  # may need local tunnel; try public
# Prefer local control if reachable
for cand in ("http://127.0.0.1:8100", "https://aimeshbackend.zeroshield.ai"):
    try:
        urllib.request.urlopen(cand + "/api/health/", timeout=5)
        CTRL = cand
        break
    except Exception:
        continue

evidence: dict = {"ctrl": CTRL, "gw": GW, "scenarios": {}}


def ctrl_login() -> str:
    req = urllib.request.Request(
        CTRL.rstrip("/") + "/api/auth/token/",
        data=json.dumps({"email": "admin@zeroshield.io", "password": "Adm1n!Pass#2024"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["access"]


def ctrl_json(method: str, path: str, token: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        CTRL.rstrip("/") + path,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:500].decode("utf-8", "replace")}
        return e.code, payload


client = OpenAI(base_url=GW, api_key=API_KEY, timeout=90.0, max_retries=0)


def chat(model: str, prompt: str = "Reply with exactly: OK") -> dict:
    t0 = time.time()
    try:
        resp = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16,
        )
        parsed = resp.parse()
        headers = {k: v for k, v in resp.headers.items() if "zeroshield" in k.lower() or k.lower() in ("x-request-id",)}
        zs = getattr(parsed, "zeroshield", None)
        return {
            "ok": True,
            "status": 200,
            "model": parsed.model,
            "content": (parsed.choices[0].message.content or "")[:80],
            "latency_s": round(time.time() - t0, 3),
            "headers": headers,
            "zeroshield": zs if isinstance(zs, dict) else getattr(zs, "__dict__", str(zs)[:200]),
            "id": parsed.id,
        }
    except APIStatusError as e:
        body = {}
        try:
            body = e.response.json()
        except Exception:
            body = {"text": (e.response.text or "")[:300]}
        return {
            "ok": False,
            "status": e.status_code,
            "latency_s": round(time.time() - t0, 3),
            "body": body,
            "code": (body.get("code") if isinstance(body, dict) else None),
        }


# --- Scenario A: existing gpt-5.2 kill-switch reroute ---
evidence["scenarios"]["A_gpt52_ks_reroute"] = chat("gpt-5.2", "Say hi in one word.")

# --- Scenario F: notifications empty ---
try:
    token = ctrl_login()
    evidence["login"] = "ok"
    st, notif = ctrl_json("GET", "/api/notifications/?limit=30", token)
    evidence["scenarios"]["F_notifications"] = {
        "status": st,
        "count": len(notif) if isinstance(notif, list) else (notif.get("results") and len(notif["results"])),
        "sample_keys": list(notif.keys()) if isinstance(notif, dict) else "list",
        "payload_preview": notif if not isinstance(notif, list) else notif[:3],
    }
except Exception as e:
    evidence["login"] = f"fail:{type(e).__name__}:{e}"
    token = None

CANARY = "gemma-4-26b-free"  # cheap; may be active

# --- Scenario B: isolate / recover canary ---
if token:
    st_i, body_i = ctrl_json(
        "POST",
        "/api/models/isolate/",
        token,
        {"model_name": CANARY, "action": "block", "reason": "m16-phase0-canary-isolate"},
    )
    evidence["scenarios"]["B_isolate_api"] = {"status": st_i, "body": body_i}
    time.sleep(1.5)
    evidence["scenarios"]["B_chat_while_isolated"] = chat(CANARY)
    st_r, body_r = ctrl_json("POST", f"/api/models/recover/{CANARY}/", token)
    evidence["scenarios"]["B_recover_api"] = {"status": st_r, "body": body_r}
    time.sleep(1.5)
    evidence["scenarios"]["B_chat_after_recover"] = chat(CANARY)

# --- Scenario D: force CB OPEN via Redis (SSH side will seed; here we just try chat after marker) ---
# Marker file written by shell after redis seed
cb_marker = Path("/tmp/m16_cb_seeded.json")
if cb_marker.exists():
    evidence["scenarios"]["D_cb_seed"] = json.loads(cb_marker.read_text())
    evidence["scenarios"]["D_chat_cb_open"] = chat(CANARY, "ping")
    # clear happens in shell after

# --- Scenario E: model states still showing isolated ---
if token:
    st, states = ctrl_json("GET", "/api/models/status/", token)
    isolated = []
    rows = states if isinstance(states, list) else states.get("results") or states.get("models") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    for m in rows or []:
        if isinstance(m, dict) and m.get("status") == "isolated":
            isolated.append(
                {
                    "model_name": m.get("model_name"),
                    "isolated_until": m.get("isolated_until"),
                    "action": m.get("action"),
                    "fallback_model": m.get("fallback_model"),
                }
            )
    evidence["scenarios"]["E_isolated_ui_rows"] = {"status": st, "isolated": isolated}

(OUT / "phase0_evidence.json").write_text(json.dumps(evidence, indent=2, default=str))
print(json.dumps({"wrote": str(OUT / "phase0_evidence.json"), "scenario_keys": list(evidence["scenarios"].keys())}, indent=2))
