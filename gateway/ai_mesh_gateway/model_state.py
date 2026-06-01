"""
ModelState enforcement and rolling risk engine for the gateway.

Checks Redis for org-scoped model state (active/isolated/degraded)
and computes rolling risk scores from guardrail verdicts.

Redis key patterns:
- ``model_state:{org_slug}:{model_name}`` -- JSON payload from Django signals
- ``model_risk:{org_slug}:{model_name}`` -- Rolling risk window (sorted set)
- ``model_risk_score:{org_slug}:{model_name}`` -- Cached composite risk score

When a model is isolated:
- ``block`` action: request rejected with 503
- ``reroute`` action: request transparently rerouted to fallback model
- ``alert`` action: request allowed but logged with warning
"""

import json
import logging
import time
from dataclasses import dataclass

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.model_state")

RISK_WINDOW_SECONDS = 300  # 5-minute rolling window
AUTO_ISOLATION_COOLDOWN = 300  # default cooldown before auto-recovery


@dataclass
class ModelStateVerdict:
    """Result of a model-state check."""

    status: str = "active"  # active, isolated, degraded
    action: str = ""  # block, reroute, alert
    fallback_model: str = ""
    reason: str = ""
    risk_score: float = 0.0
    threshold: float = 80.0


async def check_model_state(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str = "default",
) -> ModelStateVerdict:
    """
    Check model state from Redis (synced from Django ModelState).

    Returns a ModelStateVerdict indicating current status and action.
    Fail-closed on Redis errors (status ``suspended``).
    """
    key = f"model_state:{org_slug}:{model_name}"
    try:
        raw = await redis_client.get(key)
        if not raw:
            return ModelStateVerdict(status="active")

        payload = json.loads(raw if isinstance(raw, str) else raw.decode())
        status = payload.get("status", "active")
        risk_score = float(payload.get("risk_score", 0))
        threshold = float(payload.get("threshold", 80))

        if status == "isolated":
            action = payload.get("action", "block")
            fallback = payload.get("fallback_model", "")
            reason = payload.get("isolation_reason", "Model isolated")

            # Check auto-recovery: if isolated_until is set and past
            isolated_until = payload.get("isolated_until")
            if isolated_until:
                try:
                    from datetime import datetime, timezone
                    until_dt = datetime.fromisoformat(isolated_until.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) >= until_dt:
                        LOG.info(
                            "Model '%s' auto-recovery: isolated_until=%s has passed, treating as active",
                            model_name, isolated_until,
                        )
                        return ModelStateVerdict(
                            status="active",
                            risk_score=risk_score,
                            threshold=threshold,
                        )
                except (ValueError, TypeError):
                    pass

            LOG.warning(
                "Model '%s' is ISOLATED (action=%s, risk=%.1f, threshold=%.1f, reason=%s)",
                model_name, action, risk_score, threshold, reason,
            )
            return ModelStateVerdict(
                status="isolated",
                action=action,
                fallback_model=fallback,
                reason=reason,
                risk_score=risk_score,
                threshold=threshold,
            )

        if status == "degraded":
            LOG.info(
                "Model '%s' is DEGRADED (risk=%.1f, threshold=%.1f)",
                model_name, risk_score, threshold,
            )
            return ModelStateVerdict(
                status="degraded",
                action="alert",
                risk_score=risk_score,
                threshold=threshold,
            )

        return ModelStateVerdict(
            status="active",
            risk_score=risk_score,
            threshold=threshold,
        )

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        # INVARIANT 6: Model state fails closed — assume suspended when Redis is down
        LOG.error("Model state Redis check failed (fail-CLOSED): %s", exc)
        return ModelStateVerdict(status="suspended", reason=f"Redis unavailable: {exc}")
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        LOG.warning("Malformed model_state payload: %s", exc)
        return ModelStateVerdict(status="suspended", reason=f"Malformed state data: {exc}")


async def record_risk_event(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str,
    risk_value: float,
    event_type: str = "guardrail",
) -> float:
    """
    Record a risk event in the rolling window and return updated composite score.

    Uses a Redis sorted set with timestamp scores for a 5-min rolling window.
    Risk value range: 0.0 to 1.0

    Returns the updated composite risk score (0-100).
    """
    risk_key = f"model_risk:{org_slug}:{model_name}"
    score_key = f"model_risk_score:{org_slug}:{model_name}"
    now = time.time()
    cutoff = now - RISK_WINDOW_SECONDS

    try:
        pipe = redis_client.pipeline(transaction=False)
        # Add new event
        member = f"{event_type}:{now}:{risk_value}"
        pipe.zadd(risk_key, {member: now})
        # Remove events outside window
        pipe.zremrangebyscore(risk_key, 0, cutoff)
        # Get all events in window
        pipe.zrangebyscore(risk_key, cutoff, "+inf")
        # Set TTL
        pipe.expire(risk_key, RISK_WINDOW_SECONDS * 2)
        results = await pipe.execute()

        events = results[2] if len(results) > 2 else []
        if not events:
            await redis_client.set(score_key, "0.0", ex=RISK_WINDOW_SECONDS * 2)
            return 0.0

        # Compute weighted composite risk score
        total_risk = 0.0
        weights = {
            "input_scan": 0.30,
            "output_guard": 0.40,
            "anomaly": 0.20,
            "policy": 0.10,
            "guardrail": 0.25,
            "circuit_breaker": 0.35,
        }

        event_count = 0
        for evt in events:
            evt_str = evt if isinstance(evt, str) else evt.decode()
            parts = evt_str.split(":")
            if len(parts) >= 3:
                etype = parts[0]
                try:
                    val = float(parts[2])
                except (ValueError, IndexError):
                    val = 0.5
                weight = weights.get(etype, 0.25)
                total_risk += val * weight
                event_count += 1

        # Normalize to 0-100 scale
        if event_count > 0:
            # More events = higher risk (event density factor)
            density_factor = min(event_count / 10.0, 3.0)  # cap at 3x
            composite = min((total_risk / event_count) * 100 * density_factor, 100.0)
        else:
            composite = 0.0

        await redis_client.set(score_key, str(round(composite, 2)), ex=RISK_WINDOW_SECONDS * 2)

        LOG.debug(
            "Risk score for %s/%s: %.1f (events=%d, density=%.2f)",
            org_slug, model_name, composite, event_count,
            min(event_count / 10.0, 3.0),
        )

        return composite

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        # INVARIANT 6: Risk engine fails closed — assume max risk when Redis is down
        LOG.error("Risk engine Redis error (fail-CLOSED): %s", exc)
        return 100.0


async def check_auto_isolate(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str,
    current_risk: float,
    threshold: float = 80.0,
    action: str = "block",
    cooldown_seconds: int = AUTO_ISOLATION_COOLDOWN,
) -> bool:
    """
    Check if model should be auto-isolated based on risk score exceeding threshold.

    If risk > threshold, sets model_state in Redis to isolated.
    Returns True if model was just isolated.
    """
    if current_risk < threshold:
        return False

    state_key = f"model_state:{org_slug}:{model_name}"
    try:
        raw = await redis_client.get(state_key)
        if raw:
            payload = json.loads(raw if isinstance(raw, str) else raw.decode())
            if payload.get("status") == "isolated":
                return False  # Already isolated

        # Auto-isolate
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        isolated_until = (now + timedelta(seconds=cooldown_seconds)).isoformat()

        isolation_payload = {
            "status": "isolated",
            "risk_score": round(current_risk, 2),
            "threshold": threshold,
            "action": action,
            "fallback_model": "",
            "isolation_reason": f"Auto-isolated: risk score {current_risk:.1f} exceeded threshold {threshold:.1f}",
            "isolated_at": now.isoformat(),
            "isolated_until": isolated_until,
            "cooldown_seconds": cooldown_seconds,
        }
        await redis_client.set(state_key, json.dumps(isolation_payload))

        LOG.warning(
            "AUTO-ISOLATED model '%s' (org=%s): risk=%.1f > threshold=%.1f, cooldown=%ds",
            model_name, org_slug, current_risk, threshold, cooldown_seconds,
        )
        return True

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        LOG.error("Auto-isolate Redis error: %s", exc)
        return False


async def check_auto_recover(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str,
) -> bool:
    """
    Check if an isolated model should auto-recover based on cooldown expiry.

    Returns True if model was recovered.
    """
    state_key = f"model_state:{org_slug}:{model_name}"
    try:
        raw = await redis_client.get(state_key)
        if not raw:
            return False

        payload = json.loads(raw if isinstance(raw, str) else raw.decode())
        if payload.get("status") != "isolated":
            return False

        isolated_until = payload.get("isolated_until")
        if not isolated_until:
            return False

        from datetime import datetime, timezone
        until_dt = datetime.fromisoformat(isolated_until.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= until_dt:
            # Recover
            recovery_payload = {
                "status": "active",
                "risk_score": 0.0,
                "threshold": payload.get("threshold", 80.0),
                "action": payload.get("action", "block"),
                "fallback_model": payload.get("fallback_model", ""),
                "isolation_reason": "",
                "isolated_at": None,
                "isolated_until": None,
                "cooldown_seconds": payload.get("cooldown_seconds", AUTO_ISOLATION_COOLDOWN),
            }
            await redis_client.set(state_key, json.dumps(recovery_payload))
            LOG.info(
                "AUTO-RECOVERED model '%s' (org=%s): cooldown elapsed",
                model_name, org_slug,
            )
            return True

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        LOG.error("Auto-recover Redis error: %s", exc)
    return False
