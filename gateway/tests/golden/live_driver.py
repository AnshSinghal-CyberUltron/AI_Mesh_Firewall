"""Live gateway driver for chat-pipeline golden characterization."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from conftest import normalize_stages

CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")


def live_gateway_reachable() -> bool:
    try:
        r = httpx.get(f"{GATEWAY_URL}/health", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


def _login(client: httpx.Client) -> str:
    r = client.post(
        f"{CONTROL_URL}/api/auth/token/",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("access") or data.get("access_token")
    if not token:
        raise RuntimeError("login missing access token")
    return token


def _simulator_key(client: httpx.Client, jwt: str) -> str:
    r = client.post(
        f"{CONTROL_URL}/api/gateways/simulator-default/",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=60.0,
    )
    r.raise_for_status()
    key = (r.json() or {}).get("key")
    if not key:
        raise RuntimeError("simulator-default returned no key")
    return key


def _detect_model(client: httpx.Client, jwt: str) -> str:
    preset = os.environ.get("SIM_MODEL", "").strip()
    if preset:
        return preset
    r = client.get(
        f"{CONTROL_URL}/api/firewall/models/",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=60.0,
    )
    r.raise_for_status()
    body = r.json()
    models = body if isinstance(body, list) else body.get("results", [])
    active = [m for m in models if m.get("is_active", True)]
    if not active:
        raise RuntimeError("no active LLM model on org")
    return active[0].get("model_name") or active[0].get("model_id") or active[0].get("name")


def _extract_trace(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    trace = payload.get("pipeline_trace") or {}
    if not isinstance(trace, dict):
        trace = {}
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    final_action = str(trace.get("final_action") or "").strip().lower()
    if not final_action:
        zs = payload.get("zeroshield")
        if isinstance(zs, dict) and zs.get("action"):
            final_action = str(zs["action"]).lower()
    if not final_action:
        if payload.get("code") in ("content_blocked", "content_filter") or payload.get("blocked_by"):
            final_action = "block"
        elif isinstance(payload.get("error"), dict) and payload["error"].get("code") == "content_filter":
            final_action = "block"
    return stages, final_action


def characterize_live_chat(prompt: str, *, model: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    """Drive one prompt through /v1/chat/completions and return normalized observation."""
    api_key = api_key or os.environ.get("GATEWAY_API_KEY", "").strip() or None
    with httpx.Client() as client:
        if not api_key:
            jwt = _login(client)
            api_key = _simulator_key(client, jwt)
            if not model:
                model = _detect_model(client, jwt)
        model = model or os.environ.get("SIM_MODEL", "gemma-free")

        r = client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 64,
            },
            timeout=120.0,
        )
        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text[:500]}

        stages_raw, final_action = _extract_trace(payload if isinstance(payload, dict) else {})
        http_status = r.status_code

        if not final_action:
            if http_status in (400, 403, 451):
                final_action = "block"
            elif http_status == 200:
                final_action = "allow"

        norm_stages = normalize_stages(
            [
                {
                    **st,
                    "stage": st.get("name") or st.get("stage"),
                }
                for st in stages_raw
                if isinstance(st, dict)
            ]
        )

        return {
            "http_status": http_status,
            "final_action": final_action,
            "stages": norm_stages,
            "raw_trace": stages_raw,
            "payload_excerpt": json.dumps(payload, default=str)[:800] if isinstance(payload, dict) else str(payload)[:800],
        }
