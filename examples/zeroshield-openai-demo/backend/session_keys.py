"""Per-org gateway API key cache for demo sessions.

After console login, provision the org simulator key via control
``POST /api/gateways/simulator-default/`` (same as Attack Simulator).
Plaintext keys stay server-side only; the browser sees key prefix.
"""
from __future__ import annotations

import threading
from typing import Any

import httpx
from fastapi import HTTPException

from config import CONTROL_BASE_URL

_LOCK = threading.Lock()
# token → {org_id, org_slug, org_name, key, prefix, email}
_SESSIONS: dict[str, dict[str, Any]] = {}


def clear_session(token: str) -> None:
    with _LOCK:
        _SESSIONS.pop(token, None)


def get_session(token: str) -> dict[str, Any] | None:
    with _LOCK:
        return dict(_SESSIONS[token]) if token in _SESSIONS else None


def ensure_gateway_key(token: str, user: dict) -> dict[str, Any]:
    """Return session dict with gateway key; provision via control if needed."""
    with _LOCK:
        existing = _SESSIONS.get(token)
        if existing and existing.get("key"):
            return dict(existing)

    org = user.get("organization") or {}
    org_id = org.get("id")
    url = f"{CONTROL_BASE_URL}/api/gateways/simulator-default/"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"ensure": "1"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not provision org gateway key (control unreachable)",
        ) from exc

    if resp.status_code == 404:
        raise HTTPException(
            status_code=503,
            detail="Simulator gateway keys are disabled on this control plane",
        )
    if resp.status_code >= 400:
        detail = "Failed to provision org gateway key"
        try:
            detail = resp.json().get("detail") or detail
        except Exception:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)

    data = resp.json()
    raw_key = data.get("key")
    if not raw_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Org simulator key exists but plaintext was not returned. "
                "Ask an admin to rotate the simulator key, then try again."
            ),
        )

    session = {
        "org_id": data.get("org_id") or org_id,
        "org_slug": data.get("org_slug") or org.get("slug"),
        "org_name": org.get("name") or data.get("org_slug"),
        "key": raw_key,
        "prefix": data.get("prefix") or raw_key[:8],
        "email": user.get("email"),
        "user_id": user.get("id"),
    }
    with _LOCK:
        _SESSIONS[token] = session
    return dict(session)


def gateway_key_for_user(user: dict) -> tuple[str, dict[str, Any]]:
    """Resolve OpenAI api_key + session metadata from require_user() result."""
    token = user.get("_access_token") or ""
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    session = ensure_gateway_key(token, user)
    return session["key"], session
