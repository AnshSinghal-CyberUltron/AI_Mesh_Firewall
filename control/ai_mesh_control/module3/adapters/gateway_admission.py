"""Call gateway admission verify endpoint from Django control plane."""

from __future__ import annotations

import json
import logging
import os

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _gateway_base_url() -> str:
    return (
        (getattr(settings, "GATEWAY_URL", "") or "").strip().rstrip("/")
        or os.environ.get("GATEWAY_URL", "").strip().rstrip("/")
        or "http://gateway:8300"
    )


def _gateway_internal_secret() -> str:
    return (
        (getattr(settings, "GATEWAY_INTERNAL_API_KEY", "") or "").strip()
        or os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
    )


def verify_admission(payload: dict) -> dict:
    """
    POST to /v1/admin/admission/verify on the gateway.
    Returns dict with allowed, reason, verified_at, latency_ms.
    Raises RuntimeError when gateway is unreachable or misconfigured.
    """
    secret = _gateway_internal_secret()
    url = f"{_gateway_base_url()}/v1/admin/admission/verify"
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Gateway-Internal-Key"] = secret

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
    except requests.RequestException as exc:
        logger.warning("Gateway admission verify failed: %s", exc)
        raise RuntimeError(f"Gateway unreachable: {exc}") from exc

    try:
        body = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Gateway returned non-JSON: {resp.text[:500]}") from exc

    if resp.status_code >= 500:
        raise RuntimeError(body.get("reason") or body.get("error") or f"HTTP {resp.status_code}")

    # 403 is expected for denied admissions — body still contains allowed=false
    return body
