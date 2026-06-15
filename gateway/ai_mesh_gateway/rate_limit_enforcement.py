"""
Phase 1 §1.1 — per-org TPM rate-limit enforcement helper.

Extracted from main.py so it can be unit-tested in isolation on Python 3.9
(main.py transitively imports modules using PEP 604 ``X | None`` syntax which
does not parse on 3.9).

The helper is **fail-OPEN** (L6): if the limiter is configured but a backend
(Redis) error occurs during the check, the request is ALLOWED. Rate limiting
is best-effort capacity protection, not a security boundary — Tier-1/Tier-2
content scanning still runs — so a transient Redis outage must not mass-block
every tenant. A 429 is returned only on a clean over-limit result.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse

LOG = logging.getLogger("gateway.rate_limit_enforcement")


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
        # L6: fail-OPEN — a transient Redis outage/slowness must NOT mass-block
        # every tenant's traffic. The rate limiter is best-effort capacity
        # protection, not a security boundary (tier-1/tier-2 scanning still runs),
        # so on a limiter backend error we ALLOW the request. Consistent with the
        # burst/RPM paths and the now-corrected module docstring.
        org_allowed, org_current = True, 0

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


async def enforce_org_burst_rpm(
    auth_ctx: Any,
    *,
    redis_client: Any,
    config_sync: Any,
    gateway_config: dict,
    metrics: dict,
    emit_telemetry: Callable[..., None],
    event_type: str,
    model: str = "",
    user_id: Optional[int] = None,
    project_id: str = "",
) -> Optional[JSONResponse]:
    """
    Enforce per-org burst (req/s) and RPM (req/min) ceilings.

    This is the shared extraction of the burst+RPM block that previously lived
    ONLY inside ``proxy_chat``. The RAG (``/v1/rag*``), embeddings
    (``/v1/embeddings``) and ingest paths called only the per-org TPM helper —
    and ``org_tpm_limit`` is effectively always 0 — so they had *no* working
    request-rate ceiling. Call this at the top of those handlers (after auth)
    to apply the same burst/RPM protection the chat hot path enjoys.

    Behaviour mirrors the proxy_chat block exactly:
      * Reads ``org_config`` from ``config_sync`` (per-org firewall config).
      * Honors ``enforcement_mode`` — only ``"block"`` returns a 429; otherwise
        a ceiling breach is logged (monitor mode) and the request proceeds.
      * Fail-OPEN on Redis errors (matches proxy_chat): a transient Redis blip
        must not take down RAG/embedding traffic. The TPM, burst, and RPM
        limits are ALL best-effort capacity dampeners (fail-open), not a
        security boundary — content scanning is the security gate.

    Returns a 429 JSONResponse to short-circuit the request, or None to proceed.
    """
    if redis_client is None or auth_ctx is None:
        return None
    org_slug = getattr(auth_ctx, "org_slug", "") or ""

    org_config: dict = {}
    if config_sync is not None and org_slug:
        try:
            org_config = config_sync.get_config(org_slug) or {}
        except Exception:
            org_config = {}

    gateway_config = gateway_config or {}
    # Respect the master rate-limit toggle (org override → gateway default → on).
    if not org_config.get(
        "rate_limit_enabled", gateway_config.get("rate_limit_enabled", True)
    ):
        return None
    if org_config.get("firewall_enabled") is False:
        return None

    enforcement_mode = str(
        org_config.get("enforcement_mode", gateway_config.get("enforcement_mode", "block"))
    )
    # Unauthenticated callers (empty org_slug) share one "global" bucket so the
    # Redis key is never "ratelimit::...".
    rl_scope = org_slug or "global"
    now_ts = int(time.time())
    key_prefix = getattr(auth_ctx, "prefix", "") or ""

    # ── Burst (req/s) ──
    try:
        burst_limit = int(
            org_config.get("burst_limit", gateway_config.get("burst_limit", 150))
        )
        burst_key = f"ratelimit:{rl_scope}:burst:{now_ts}"
        current_burst = await redis_client.incr(burst_key)
        if current_burst == 1:
            await redis_client.expire(burst_key, 2)
        if current_burst > burst_limit:
            if enforcement_mode == "block":
                metrics["blocked"] = metrics.get("blocked", 0) + 1
                try:
                    emit_telemetry(
                        status_code=429,
                        event_type=event_type,
                        model=model,
                        user_id=user_id,
                        project_id=project_id,
                        key_prefix=key_prefix,
                        action="block",
                        risk_score=0.30,
                        threat_type="rate_limit_burst",
                        metadata={
                            "detail": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s)",
                            "org_slug": org_slug,
                        },
                    )
                except Exception:
                    pass
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s).",
                        "code": "burst_limit_exceeded",
                    },
                    headers={"Retry-After": "1"},
                )
            else:
                LOG.warning(
                    "MONITOR: burst limit exceeded (%d/%d req/s) org=%s",
                    current_burst, burst_limit, org_slug,
                )
    except Exception:
        # Fail-OPEN: never let a Redis blip drop RAG/embedding traffic.
        LOG.warning("Burst rate check failed, skipping", exc_info=True)

    # ── RPM (req/min) ──
    try:
        rpm_limit = int(
            org_config.get("requests_per_minute", gateway_config.get("requests_per_minute", 1000))
        )
        minute_bucket = now_ts // 60
        rpm_key = f"ratelimit:{rl_scope}:{minute_bucket}"
        current_rpm = await redis_client.incr(rpm_key)
        if current_rpm == 1:
            await redis_client.expire(rpm_key, 120)
        if current_rpm > rpm_limit:
            if enforcement_mode == "block":
                metrics["blocked"] = metrics.get("blocked", 0) + 1
                try:
                    emit_telemetry(
                        status_code=429,
                        event_type=event_type,
                        model=model,
                        user_id=user_id,
                        project_id=project_id,
                        key_prefix=key_prefix,
                        action="block",
                        risk_score=0.30,
                        threat_type="rate_limit_rpm",
                        metadata={
                            "detail": f"RPM limit exceeded ({current_rpm}/{rpm_limit})",
                            "org_slug": org_slug,
                        },
                    )
                except Exception:
                    pass
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": f"Rate limit exceeded ({current_rpm}/{rpm_limit} RPM).",
                        "code": "rate_limit_exceeded",
                    },
                    headers={"Retry-After": "60"},
                )
            else:
                LOG.warning(
                    "MONITOR: RPM limit exceeded (%d/%d) org=%s",
                    current_rpm, rpm_limit, org_slug,
                )
    except Exception:
        LOG.warning("RPM check failed, skipping", exc_info=True)

    return None
