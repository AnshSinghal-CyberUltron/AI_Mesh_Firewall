"""Control-plane login for the demo (same credentials as console /login).

Auth exception to the SDK-only rule: we verify email/password against control
``POST /api/auth/token/`` and load org membership from ``GET /api/auth/me/``.
Any authenticated user with an organization may use the demo — AI traffic then
goes through that org's gateway key via the OpenAI SDK only.
"""
from __future__ import annotations

import httpx
from fastapi import Header, HTTPException

from config import CONTROL_BASE_URL


def _bearer(authorization: str) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def _control_me(token: str) -> dict | None:
    url = f"{CONTROL_BASE_URL}/api/auth/me/"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def require_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: authenticated control user with an organization."""
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = _control_me(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    org = user.get("organization")
    if not org or not org.get("id"):
        raise HTTPException(
            status_code=403,
            detail="Organization membership required for the OpenAI SDK demo",
        )
    user["_access_token"] = token
    return user


def authenticate(email: str, password: str) -> dict:
    """Log in against control; return {access, refresh, user}."""
    token_url = f"{CONTROL_BASE_URL}/api/auth/token/"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(token_url, json={"email": email, "password": password})
            if resp.status_code != 200:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            tokens = resp.json()
            access = tokens.get("access")
            if not access:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            me = client.get(
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
    org = user.get("organization")
    if not org or not org.get("id"):
        raise HTTPException(
            status_code=403,
            detail="Organization membership required for the OpenAI SDK demo",
        )
    return {
        "access": access,
        "refresh": tokens.get("refresh"),
        "user": user,
    }
