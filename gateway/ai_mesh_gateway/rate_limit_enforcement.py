"""
Phase 1 §1.1 — per-org TPM rate-limit enforcement helper.

Extracted from main.py so it can be unit-tested in isolation on Python 3.9
(main.py transitively imports modules using PEP 604 ``X | None`` syntax which
does not parse on 3.9).

The helper is **fail-CLOSED**: if the limiter is configured and any error
occurs during the check, the request is rejected with 429. This is the
correct posture for tenant isolation — better to drop a request than to
allow a noisy tenant to exhaust shared upstream quota.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse


async def enforce_org_tpm_rate_limit(
    auth_ctx: Any,
    *,
    rate_limiter: Any,
    config_sync: Any,
    metrics: dict,
    emit_telemetry: Callable[..., None],
    event_type: str,
    model: str = "",
    user_id: Optional[int] = None,
    project_id: str = "",
    estimated_tokens: int = 20,
) -> Optional[JSONResponse]:
    """
    Enforce the per-org TPM ceiling. Returns a 429 JSONResponse to short-circuit
    the request, or None to proceed.

    Args:
        auth_ctx: authenticated API-key context (must expose ``org_slug``).
        rate_limiter: gateway RATE_LIMITER (or None to no-op).
        config_sync: CONFIG_SYNC for per-org config lookup.
        metrics: shared METRICS dict (``blocked`` counter incremented on block).
        emit_telemetry: bound ``_emit_telemetry`` callable from main.
        event_type: telemetry event type, e.g. "embedding_blocked".
        model, user_id, project_id: telemetry attribution fields.
        estimated_tokens: tokens to charge against the bucket on this request.
    """
    if rate_limiter is None or auth_ctx is None:
        return None
    org_slug = getattr(auth_ctx, "org_slug", "") or ""
    if not org_slug:
        return None

    org_tpm_limit = 0
    if config_sync is not None:
        try:
            org_cfg = config_sync.get_config(org_slug) or {}
            org_tpm_limit = int(org_cfg.get("org_tpm_limit", 0) or 0)
        except Exception:
            org_tpm_limit = 0
    if not org_tpm_limit:
        return None

    try:
        org_allowed, org_current = await rate_limiter.check_org_rate_limit(
            org_slug, org_tpm_limit, estimated_tokens,
        )
    except Exception:
        # fail-CLOSED — drop the request rather than risk cross-tenant abuse
        org_allowed, org_current = False, org_tpm_limit

    if org_allowed:
        return None

    metrics["blocked"] = metrics.get("blocked", 0) + 1
    try:
        emit_telemetry(
            status_code=429,
            event_type=event_type,
            model=model,
            user_id=user_id,
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", "") or "",
            action="block",
            risk_score=0.30,
            threat_type="rate_limit_org_tpm",
            metadata={
                "detail": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit})",
                "org_slug": org_slug,
                "module": "1.1",
                "module_id": "1.1",
            },
        )
    except Exception:
        pass

    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "scope": "org",
            "message": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit}).",
            "code": "org_rate_limit_exceeded",
        },
        headers={"Retry-After": "60"},
    )
