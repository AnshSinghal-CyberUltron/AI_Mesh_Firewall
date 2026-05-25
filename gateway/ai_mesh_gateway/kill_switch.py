"""
Gateway-local kill-switch enforcement.

Checks Redis for global and per-model kill-switch status on every
request. Two pipelined GETs per request (~0.1ms overhead).

Redis key patterns:
- ``kill_switch:global`` -- JSON payload for global kill-switch
- ``kill_switch:model:{model_name}`` -- JSON payload for per-model switches

When a kill-switch is active:
- ``disable`` action: request rejected with 503
- ``reroute`` action: request transparently rerouted to fallback model
"""

import json
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.kill_switch")

GLOBAL_KEY = "kill_switch:global"
MODEL_KEY_PREFIX = "kill_switch:model:"


@dataclass
class KillSwitchVerdict:
    """Result of a kill-switch check."""

    is_killed: bool = False
    action: str = ""
    fallback_model: str = ""
    reason: str = ""


async def check_kill_switch(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str = "",
) -> KillSwitchVerdict:
    """
    Check global and per-model kill switches via Redis pipeline.

    Returns a KillSwitchVerdict indicating whether the model is killed
    and what action to take (disable or reroute).

    Global kill-switch takes precedence over per-model.
    On Redis failure, returns allow (fail-open for kill-switch checks,
    since the security posture is maintained by other layers).
    """
    prefix = org_slug or "default"
    try:
        global_key = f"kill_switch:{prefix}:global"
        model_key = f"kill_switch:{prefix}:model:{model_name}"
        async with redis_client.pipeline(transaction=False) as pipe:
            pipe.get(global_key)
            pipe.get(model_key)
            results = await pipe.execute()

        global_raw, model_raw = results

        if global_raw:
            payload = _parse_payload(global_raw)
            if payload and payload.get("is_active"):
                LOG.warning(
                    "Global kill-switch active (reason: %s)",
                    payload.get("reason", ""),
                )
                return KillSwitchVerdict(
                    is_killed=True,
                    action="disable",
                    fallback_model="",
                    reason=payload.get("reason", "Global kill-switch active"),
                )

        if model_raw:
            payload = _parse_payload(model_raw)
            if payload and payload.get("is_active"):
                action = payload.get("action", "disable")
                fallback = payload.get("fallback_model", "")
                reason = payload.get("reason", "")
                LOG.warning(
                    "Kill-switch active for model '%s' (action=%s, fallback=%s, reason=%s)",
                    model_name,
                    action,
                    fallback,
                    reason,
                )
                return KillSwitchVerdict(
                    is_killed=True,
                    action=action,
                    fallback_model=fallback,
                    reason=reason,
                )

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        # INVARIANT 6: Kill-switch fails closed — block on Redis errors
        LOG.error("Kill-switch Redis check failed (fail-CLOSED): %s", exc)
        return KillSwitchVerdict(
            is_killed=True,
            action="disable",
            fallback_model="",
            reason=f"Kill-switch Redis unavailable — fail-closed: {exc}",
        )

    return KillSwitchVerdict(is_killed=False)


def _parse_payload(raw: str) -> dict | None:
    """Parse a JSON payload from Redis, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        LOG.warning("Malformed kill-switch payload in Redis: %s", raw[:100])
        return None
