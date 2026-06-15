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

# Mirrors control's KILL_SWITCH_ACTION_CHOICES (core/models.py). Any other
# value in a Redis payload indicates corruption and the entry is ignored.
VALID_ACTIONS = frozenset({"disable", "reroute"})


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
    Malformed payloads (non-dict JSON, unknown/missing action, reroute
    without fallback) are ignored — FAIL-OPEN — so a corrupt entry cannot
    crash the request path or deny all traffic for a model.
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
            payload = _parse_payload(raw, redis_key)
            if payload is None:
                # FAIL-OPEN: a corrupt entry must not crash the request path
                # or deny all traffic for a model. _parse_payload already
                # logged the key and the rejection reason.
                continue
            if not payload.get("is_active"):
                continue

            action = payload.get("action")
            if action not in VALID_ACTIONS:
                LOG.warning(
                    "Ignoring kill-switch entry at %s: unknown/missing action %r "
                    "(expected one of %s) — fail-open",
                    redis_key,
                    action,
                    sorted(VALID_ACTIONS),
                )
                continue
            fallback = str(payload.get("fallback_model") or "").strip()
            if action == "reroute" and not fallback:
                LOG.warning(
                    "Ignoring kill-switch entry at %s: action='reroute' with no "
                    "fallback_model (nowhere to reroute) — fail-open",
                    redis_key,
                )
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

            reason = sanitize_kill_switch_reason(payload.get("reason", ""))
            LOG.warning(
                "Kill-switch active scope=%s model='%s' action=%s fallback=%s reason=%s",
                scope_name,
                model_name,
                action,
                fallback,
                reason,
            )
            scope_label = "credential" if scope_name == "credential" else "org_model"
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


def _parse_payload(raw: str | bytes, redis_key: str = "") -> dict | None:
    """
    Parse a JSON object payload from Redis.

    Returns None (entry is ignored — fail-open) when the value is not valid
    JSON or decodes to anything other than a dict ('123', '[1]', '"x"', …),
    so callers can rely on dict semantics without an AttributeError.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        LOG.warning(
            "Ignoring malformed kill-switch payload at %s: not valid JSON (%.100s)",
            redis_key,
            str(raw),
        )
        return None
    if not isinstance(payload, dict):
        LOG.warning(
            "Ignoring malformed kill-switch payload at %s: expected JSON object, "
            "got %s (%.100s)",
            redis_key,
            type(payload).__name__,
            str(raw),
        )
        return None
    return payload
