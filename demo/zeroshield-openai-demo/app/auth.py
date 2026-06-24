"""Superuser auth gate for the demo.

The demo is a privileged internal tool, so every data route requires a platform
SUPERUSER. We never mint or store credentials here — the caller's control JWT is
verified against control's ``/api/auth/me/`` and ``is_superuser`` is enforced.
Defense in depth: ``/api/login`` rejects non-superusers at sign-in, and
``require_superuser`` re-checks on every protected request.
"""
from __future__ import annotations

import httpx
from fastapi import Header, HTTPException

from app.config import CONTROL_BASE_URL


def _bearer(authorization: str) -> str:
    """Extract the raw token from an Authorization header (with or without Bearer)."""
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


async def _control_me(token: str) -> dict | None:
    """Return the control user dict for a valid access token, else None.

    Raises 503 only when control itself is unreachable (fail-closed: an
    unreachable auth service must never silently grant access).
    """
    url = f"{CONTROL_BASE_URL}/api/auth/me/"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def require_superuser(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: allow only authenticated control superusers."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await _control_me(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if not user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Superuser access required")
    return user


async def authenticate(email: str, password: str) -> dict:
    """Log in against control, enforce superuser, return {access, refresh, user}."""
    token_url = f"{CONTROL_BASE_URL}/api/auth/token/"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, json={"email": email, "password": password})
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            tokens = resp.json()
            access = tokens.get("access")
            if not access:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            me = await client.get(
                f"{CONTROL_BASE_URL}/api/auth/me/",
                headers={"Authorization": f"Bearer {access}"},
            )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if me.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = me.json()
    if not user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Superuser access required")
    return {"access": access, "refresh": tokens.get("refresh"), "user": user}
