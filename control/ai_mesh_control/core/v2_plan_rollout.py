"""Deterministic rollout of the gateway-v2 plan documents (F4 R1; PR #115 reviews A-S2, B-S4).

``publish_for_org`` runs only when an Organization or FirewallConfig is saved
(``core/signals.py``), from the console views, or from ``manage.py sync_v2_plan_documents``, which
nothing ran. So when what the publisher writes changes, through a release (the F4 extension rows)
or a deployment setting it reads (``V2_PLAN_NEW_RULES_MODE`` and the others in
:data:`PUBLISH_SETTINGS`), an org that does not save keeps its old document indefinitely and the
fleet is silently mixed. That also defeated the rollback "set the gate to ``off``".

:func:`reconcile` closes that:

* :func:`fingerprint` hashes what the running publisher writes: a probe document built by the
  current code under the current settings, plus the settings themselves.
* It is compared with the fingerprint of the last COMPLETE sweep, kept in Redis. Equal: nothing to
  do (one GET), so a restart costs nothing.
* Different: one runner, elected with a Redis lock, republishes every active org in primary-key
  order, :data:`BATCH` orgs per query, so memory stays bounded at any org count. Only a sweep with
  no failure records the new fingerprint; a failed or interrupted sweep repeats on the next run.
  Repeating is safe: ``_store`` raises an org's revision only when its body changed.

It runs once per control container start (``server-entrypoint.sh``, in the background, so the
health check never waits on a long sweep) and from ``manage.py sync_v2_plan_documents
--if-changed``. A gate flip and a rollback are environment changes, and an environment change takes
effect only when the container is recreated, so the same start-up run covers deploy, flip and
rollback. Running per container rather than per gunicorn worker matters: workers are recycled
(``--max-requests``), and a recycled worker of an OLD container must never republish with the old
settings after a new container has swept.

A lost Redis marker costs one extra, idempotent sweep. Losing the lock mid-sweep (TTL expiry) can
let a second runner sweep concurrently; each org is published inside its own row lock
(``publish_for_org``), so the two produce the same documents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Redis key holding the fingerprint of the last complete sweep.
MARKER_KEY = "v2plan:rollout:fingerprint"
#: Redis key electing the one runner. Its TTL is refreshed after every batch.
LOCK_KEY = "v2plan:rollout:lock"
LOCK_TTL_S = 300
#: Orgs fetched per query (keyset pagination on the primary key).
BATCH = 500
#: Consecutive failed publishes that end a sweep early. A systemic failure (database down, schema
#: not yet migrated) fails every org, and ``publish_for_org`` logs a traceback for each one, so
#: without this a 100k-org fleet would log 100k tracebacks per attempt. One bad org does not stop
#: the sweep; the sweep is incomplete either way, so it is retried.
MAX_CONSECUTIVE_FAILURES = 50
#: Deployment settings the publisher reads (``firewall_v2_map._setting``,
#: ``firewall_v2_controls._setting``, ``firewall_v2_publish._dev_copy``). A change to any of them
#: changes some org's document, so each is part of the fingerprint.
PUBLISH_SETTINGS = (
    "V2_PLAN_NEW_RULES_MODE",
    "V2_PLAN_CONTROLS_ENABLED",
    "V2_PLAN_CONTROLS_ORGS",
    "V2_REWRITE_ORGS",
    "V2_EXFIL_ORGS",
    "V2_EXFIL_ACTION",
    "V2_EXFIL_ALLOW_HOSTS",
    "V2_CANARY_ORGS",
    "V2_CANARY_STRICT_ORGS",
    "AMF_V2_AUDIT_ORG_MAP",
)
_PROBE_SLUG = "_v2-plan-fingerprint"
#: Every built-in the console can turn on, so the probe document carries every row kind the
#: publisher emits (category rows, threat rows, their extension rows, output detector rows,
#: a rewrite row, the keyword policy row and the controls object).
_PROBE: dict[str, Any] = {
    "firewall_enabled": True,
    "enforcement_mode": "block",
    "content_filtering_enabled": True,
    "pii_detection_enabled": True,
    "jailbreak_detection_enabled": True,
    "response_filtering_enabled": True,
    "output_pii_enabled": True,
    "output_pii_action": "rewrite",
    "output_credential_enabled": True,
    "output_credential_action": "block",
    "output_ip_leakage_enabled": True,
    "factuality_check_enabled": True,
    "threat_intel_enabled": True,
    "auto_block_threats": True,
    "threat_score_threshold": 75,
    "blocked_keywords": "password, token",
    "rate_limit_enabled": True,
    "requests_per_minute": 1000,
    "burst_limit": 150,
}


class _Redis(Protocol):
    def get(self, name: str) -> Any: ...

    def set(self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...) -> Any: ...

    def expire(self, name: str, time: int) -> Any: ...

    def delete(self, *names: str) -> Any: ...


def _redis() -> _Redis:
    from core.signals import _get_redis_client

    return _get_redis_client()


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _settings() -> dict[str, str]:
    from core.firewall_v2_controls import _setting as controls_setting
    from core.firewall_v2_controls import new_rules_mode
    from core.firewall_v2_map import _setting as map_setting

    out = {name: map_setting(name) for name in PUBLISH_SETTINGS}
    out["rules_mode"] = new_rules_mode()
    for name in ("V2_PLAN_CONTROLS_ENABLED", "V2_PLAN_CONTROLS_ORGS"):
        out[f"django:{name}"] = _text(controls_setting(name, ""))
    return out


def fingerprint() -> str:
    """sha256 over the probe document the running code publishes and the publish settings."""
    from core.firewall_v2_controls import new_rules_mode
    from core.firewall_v2_publish import document_bytes, keyword_row, policy_drafts

    mode = new_rules_mode()
    keyword = keyword_row(_PROBE, mode)
    rows, _unsupported = policy_drafts([keyword] if keyword else [], _PROBE_SLUG)
    document = document_bytes(
        _PROBE_SLUG, _PROBE, rows, _PROBE_SLUG, controls_enabled=True, rules_mode=mode,
    )
    blob = json.dumps(
        {"document": document.decode("utf-8"), "settings": _settings()},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def sweep(on_batch: Callable[[], None] | None = None) -> tuple[int, int]:
    """Republish every active org, :data:`BATCH` per query. Returns ``(published, failed)``.

    Stops after :data:`MAX_CONSECUTIVE_FAILURES` failures in a row (``failed`` is then non-zero).
    """
    from auth.models import Organization
    from core.firewall_v2_publish import publish_for_org

    published = failed = streak = 0
    last = 0
    while True:
        batch = list(
            Organization.objects.filter(is_active=True, pk__gt=last)
            .order_by("pk")
            .only("pk", "slug")[:BATCH]
        )
        if not batch:
            return published, failed
        for org in batch:
            if publish_for_org(org).get("status") == "published":
                published += 1
                streak = 0
                continue
            failed += 1
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "gateway-v2 plan rollout stopped after %d consecutive failures", streak
                )
                return published, failed
        last = batch[-1].pk
        if on_batch is not None:
            on_batch()


def reconcile(*, client: _Redis | None = None, force: bool = False) -> dict[str, object]:
    """Republish every active org once if the publisher's fingerprint changed. Idempotent.

    ``force`` sweeps even when the marker matches (the manual ``sync_v2_plan_documents``).
    Statuses: ``current`` (nothing to do), ``locked`` (another runner holds the lock),
    ``swept`` (complete; marker written) or ``incomplete`` (some org failed; marker unchanged).
    """
    redis = client if client is not None else _redis()
    current = fingerprint()
    if not force and _text(redis.get(MARKER_KEY)) == current:
        return {"status": "current", "fingerprint": current}
    token = secrets.token_hex(8)
    if not redis.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_S):
        return {"status": "locked", "fingerprint": current}
    started = time.monotonic()
    try:
        published, failed = sweep(lambda: redis.expire(LOCK_KEY, LOCK_TTL_S))
        if failed == 0:
            redis.set(MARKER_KEY, current)
    finally:
        if _text(redis.get(LOCK_KEY)) == token:
            redis.delete(LOCK_KEY)
    note: dict[str, object] = {
        "status": "swept" if failed == 0 else "incomplete",
        "fingerprint": current,
        "published": published,
        "failed": failed,
        "seconds": round(time.monotonic() - started, 3),
    }
    logger.info("gateway-v2 plan rollout: %s", note)
    return note


def reconcile_until_settled(
    *,
    attempts: int,
    wait_s: float,
    client: _Redis | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """:func:`reconcile` until ``current`` or ``swept``, at most ``attempts`` times.

    Retries cover a container that starts before its migrations ran (``deploy-ec2.sh`` migrates
    after control is healthy), a database or Redis that is not up yet, and a runner on another
    replica that holds the lock and then dies before writing the marker.
    """
    note: dict[str, object] = {"status": "not_run"}
    for attempt in range(1, max(attempts, 1) + 1):
        try:
            note = reconcile(client=client)
        except Exception:
            logger.warning("gateway-v2 plan rollout attempt %d failed", attempt, exc_info=True)
            note = {"status": "error"}
        if note.get("status") in ("current", "swept"):
            return note
        if attempt < attempts:
            sleep(wait_s)
    return note
