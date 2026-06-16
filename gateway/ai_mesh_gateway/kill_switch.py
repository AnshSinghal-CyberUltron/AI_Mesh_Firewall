"""
Gateway-local kill-switch enforcement.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.kill_switch")

GLOBAL_SUFFIX = "global"
MODEL_KEY_PREFIX = "model:"
CREDENTIAL_KEY_PREFIX = "credential:"


@dataclass
class KillSwitchVerdict:
    """Result of a kill-switch check."""

    is_killed: bool = False
    action: str = ""
    fallback_model: str = ""
    reason: str = ""
    scope: str = ""  # credential | org_model | org_global | redis_unavailable


async def check_kill_switch(
    redis_client: aioredis.Redis,
    model_name: str,
    org_slug: str = "",
    key_prefix: str = "",
) -> KillSwitchVerdict:
    """
    Check kill switches via Redis pipeline (per-request canonical read).

    Precedence: credential-scoped > per-model > global.
    On Redis failure, fail-closed (disable).
    """
    prefix = org_slug or "default"
    keys: list[tuple[str, str]] = []

    if key_prefix:
        keys.append(
            (
                "credential",
                f"kill_switch:{prefix}:{CREDENTIAL_KEY_PREFIX}{key_prefix}:{MODEL_KEY_PREFIX}{model_name}",
            )
        )
        keys.append(
            (
                "credential_all",
                f"kill_switch:{prefix}:{CREDENTIAL_KEY_PREFIX}{key_prefix}",
            )
        )
    keys.append(("org_model", f"kill_switch:{prefix}:{MODEL_KEY_PREFIX}{model_name}"))
    keys.append(("org_global", f"kill_switch:{prefix}:{GLOBAL_SUFFIX}"))

    try:
        async with redis_client.pipeline(transaction=False) as pipe:
            for _, redis_key in keys:
                pipe.get(redis_key)
            results = await pipe.execute()

        for (scope_name, redis_key), raw in zip(keys, results):
            if not raw:
                continue
            payload = _parse_payload(raw)
            if payload is None:
                LOG.error(
                    "Malformed kill-switch payload at %s — fail-CLOSED",
                    redis_key,
                )
                return KillSwitchVerdict(
                    is_killed=True,
                    action="disable",
                    fallback_model="",
                    reason="Kill-switch payload corrupt — fail-closed",
                    scope="malformed_payload",
                )
            if not payload.get("is_active"):
                continue

            if scope_name == "org_global":
                LOG.warning(
                    "Global kill-switch active (reason: %s)",
                    payload.get("reason", ""),
                )
                return KillSwitchVerdict(
                    is_killed=True,
                    action="disable",
                    fallback_model="",
                    reason=payload.get("reason", "Global kill-switch active"),
                    scope="org_global",
                )

            action = payload.get("action", "disable")
            fallback = payload.get("fallback_model", "")
            reason = sanitize_kill_switch_reason(payload.get("reason", ""))
            LOG.warning(
                "Kill-switch active scope=%s model='%s' action=%s fallback=%s reason=%s",
                scope_name,
                model_name,
                action,
                fallback,
                reason,
            )
            scope_label = (
                "credential" if scope_name in ("credential", "credential_all") else "org_model"
            )
            return KillSwitchVerdict(
                is_killed=True,
                action=action,
                fallback_model=fallback,
                reason=reason or f"Kill-switch active ({scope_label})",
                scope=scope_label,
            )

    except (aioredis.RedisError, OSError, ConnectionError) as exc:
        LOG.error("Kill-switch Redis check failed (fail-CLOSED): %s", exc)
        return KillSwitchVerdict(
            is_killed=True,
            action="disable",
            fallback_model="",
            reason=f"Kill-switch Redis unavailable — fail-closed: {exc}",
            scope="redis_unavailable",
        )

    return KillSwitchVerdict(is_killed=False, scope="none")


def sanitize_kill_switch_reason(reason: str, max_len: int = 500) -> str:
    """Strip control chars and cap length before logs/telemetry (defense-in-depth)."""
    if not reason:
        return ""
    cleaned = "".join(ch for ch in str(reason) if ch.isprintable() or ch in "\n\t")
    return cleaned[:max_len]


def _parse_payload(raw: str) -> dict | None:
    """Parse a JSON payload from Redis, returning None on failure."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        LOG.warning("Malformed kill-switch payload in Redis: %s", str(raw)[:100])
        return None
