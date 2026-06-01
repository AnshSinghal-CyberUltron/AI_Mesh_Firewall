"""
Admin RBAC helper for the AI Mesh Gateway.

Extracted into its own module so it is importable + unit-testable on
Python 3.9 without dragging in main.py's PEP 604 dependency chain
(same pattern as ``rate_limit_enforcement.py``).

Security policy
---------------
- Fail CLOSED: any unexpected error rejects the request.
- Two independent signals grant admin access:
    1. ``auth_ctx.permissions["admin"] is True`` — forward-compatible
       RBAC flag (requires backend to mint it into the API key payload).
    2. ``str(auth_ctx.user_id)`` is listed in the
       ``GATEWAY_ADMIN_USER_IDS`` env var (comma-separated). This gives
       us immediate protection without needing a backend migration.
- A missing ``auth_context`` → 401. Present but not admin → 403.
- The env list is parsed on each call (cheap, ≤10 IDs in practice and
  this keeps tests deterministic without module-level caching).
"""
from __future__ import annotations

import os
from typing import Optional

from starlette.responses import JSONResponse


def _admin_user_ids_from_env() -> frozenset[str]:
    raw = os.environ.get("GATEWAY_ADMIN_USER_IDS", "")
    return frozenset(
        token.strip() for token in raw.split(",") if token and token.strip()
    )


def is_admin(auth_ctx) -> bool:
    """Return True iff ``auth_ctx`` represents an admin caller.

    Pure predicate — no side effects, no I/O beyond env lookup.
    Fail-CLOSED: any AttributeError / TypeError → False.
    """
    if auth_ctx is None:
        return False
    try:
        perms = getattr(auth_ctx, "permissions", None) or {}
        if isinstance(perms, dict) and perms.get("admin") is True:
            return True
        user_id = getattr(auth_ctx, "user_id", None)
        if user_id is None:
            return False
        return str(user_id) in _admin_user_ids_from_env()
    except (AttributeError, TypeError):
        return False


def require_admin(auth_ctx) -> Optional[JSONResponse]:
    """Gate helper for admin endpoints.

    Returns ``None`` when the caller is admin (proceed).
    Returns a ``JSONResponse`` (401 or 403) that the handler should
    return verbatim when the caller is not admin.

    Status codes match the existing gateway convention:
    - 401 ``unauthorized`` when no auth context is attached (request
      reached the handler without the middleware populating it — should
      not happen on admin routes, but treat defensively).
    - 403 ``forbidden`` with code ``admin_required`` when the caller is
      authenticated but lacks the admin role.
    """
    if auth_ctx is None:
        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "message": "Authentication required.",
                "code": "auth_required",
            },
        )
    if not is_admin(auth_ctx):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": "Admin role required for this endpoint.",
                "code": "admin_required",
            },
        )
    return None
