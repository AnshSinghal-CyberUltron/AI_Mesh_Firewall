"""Deterministic rollout of the gateway-v2 plan documents (F4 R1/R21; PR #115 reviews).

``publish_for_org`` runs only when an Organization or FirewallConfig is saved
(``core/signals.py``), from the console views, or from ``manage.py sync_v2_plan_documents``. So when
what the publisher writes changes (a release, a data migration of its inputs, or a deployment
setting it reads, such as ``V2_PLAN_NEW_RULES_MODE``), an org that does not save would keep its old
document indefinitely, and the rollback "set the gate to ``off``" would not reach it.

:func:`reconcile` closes that:

* :func:`fingerprint` identifies the running publisher: a sha256 over the source of every module
  that shapes a document (:data:`PUBLISHER_MODULES`), the migration files of the apps whose rows it
  reads (:data:`MIGRATION_APPS`, so a data migration such as ``core/0040`` counts), every publish
  setting (:data:`PUBLISH_SETTINGS`), and two documents it builds now: an org with every built-in
  on, and a never-saved org (the ``FirewallConfig`` model defaults through the serializer).
* Every publish stamps the document with the fingerprint of the process that built it, at the
  revision it stored (``V2PlanDocument.publisher``, ``publisher_revision``), console saves
  included.
* A document whose stamp is not the running fingerprint at its current revision is stale. That
  includes a body an older image changed after a rollback: it raises ``revision`` but does not know
  the stamp. No stale active org: done
  (one indexed query, no Redis). Otherwise, once the publisher's migrations are applied, one
  runner (an owner-checked Redis lock) republishes the stale orgs in primary-key order,
  :data:`BATCH` per query, so memory stays bounded at any org count.

The sweep is resumable by construction: an interrupted or failed run leaves only the orgs it did
not reach (or that failed) stale, and the next run republishes only those. Repeating is safe:
``_store`` raises an org's revision only when its body changed.

:func:`reconcile_until_settled` is what control runs once per container start
(``server-entrypoint.sh``, in the background, so the health check never waits on a sweep). It
retries a failed org at most :data:`ORG_MAX_TRIES` times per start; then it settles as
``partial``, logs those org ids, and leaves them stale for the next start. A gate flip and a
rollback are environment changes, which take effect only when the container is recreated, so the
same start-up run covers deploy, flip and rollback. The publish settings must be the same on every
process that publishes (control and the workers, which publish through the save signals): a
process with other settings stamps its own fingerprint, and the last control start wins.
``sync_v2_plan_documents --status`` reports how many active orgs are still stale.

Scope: this runs wherever control starts through ``server-entrypoint.sh`` (docker compose, the EC2
deploy). The Terraform ECS service overrides the container command and does not run it (register
R28).
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Redis key electing the one runner (owner-checked: release and refresh are compare-and-act).
LOCK_KEY = "v2plan:rollout:lock"
LOCK_TTL_S = 300
#: How often a running sweep refreshes its lock. A refresh that finds another owner (or none)
#: stops the sweep, so two runners never sweep side by side for longer than one org's publish.
LOCK_REFRESH_S = 60.0
#: Orgs fetched per query (keyset pagination on the primary key).
BATCH = 500
#: Consecutive failed publishes that end a sweep early. A systemic failure fails every org, and
#: ``publish_for_org`` logs a traceback for each one; this bounds the noise per attempt.
MAX_CONSECUTIVE_FAILURES = 50
#: Tries per org within one start before the org is left for the next start (``partial``).
ORG_MAX_TRIES = 3
#: Seconds between progress lines during a sweep.
PROGRESS_EVERY_S = 30.0
#: Modules whose source shapes a document: the publisher, its maps, the console payload
#: serializer, the dev-copy alias map, and this module (an edit here forces one sweep).
PUBLISHER_MODULES = (
    "core.firewall_v2_publish",
    "core.firewall_v2_map",
    "core.firewall_v2_controls",
    "core.firewall_config_serializer",
    "core.v2_audit_adapter",
    "core.v2_plan_rollout",
)
#: Apps whose rows the publisher reads (Organization, FirewallConfig, Policy). A new migration in
#: any of them (a data migration of publisher inputs emits no save signal) changes the fingerprint.
MIGRATION_APPS = ("auth_api", "core", "policy")
#: Deployment settings the publisher reads (``firewall_v2_map._setting``,
#: ``firewall_v2_controls._setting``, ``firewall_v2_publish._dev_copy``).
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
SETTLED = ("current", "swept")
_PROBE_SLUG = "_v2-plan-fingerprint"
#: Every built-in the console can turn on, so one probe document carries every row kind.
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


def _redis() -> Any:
    from core.signals import _get_redis_client

    return _get_redis_client()


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _source(module: str) -> bytes:
    spec = importlib.util.find_spec(module)
    if spec is None or not spec.origin:
        raise RuntimeError(f"publisher module {module} has no source file")
    return Path(spec.origin).read_bytes()


@functools.cache
def code_digest() -> str:
    """sha256 over the source of :data:`PUBLISHER_MODULES` (read once per process)."""
    digest = hashlib.sha256()
    for name in PUBLISHER_MODULES:
        digest.update(name.encode() + b"\0" + hashlib.sha256(_source(name)).digest())
    return digest.hexdigest()


def _migration_names(label: str) -> list[str]:
    from django.apps import apps

    folder = Path(apps.get_app_config(label).path) / "migrations"
    return [f"{label}.{path.stem}" for path in folder.glob("[0-9]*.py")]


@functools.cache
def migrations_digest() -> str:
    """sha256 over the migration file names of :data:`MIGRATION_APPS` (read once per process)."""
    names = sorted(name for label in MIGRATION_APPS for name in _migration_names(label))
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def _settings() -> dict[str, str]:
    from core.firewall_v2_controls import _setting as controls_setting
    from core.firewall_v2_controls import new_rules_mode
    from core.firewall_v2_map import _setting as map_setting

    out = {name: map_setting(name) for name in PUBLISH_SETTINGS}
    out["rules_mode"] = new_rules_mode()
    for name in ("V2_PLAN_CONTROLS_ENABLED", "V2_PLAN_CONTROLS_ORGS"):
        out[f"django:{name}"] = _text(controls_setting(name, ""))
    return out


def _documents(mode: str) -> dict[str, str]:
    """The probe documents the running publisher builds now, through the real org path."""
    from core.firewall_config_serializer import FirewallConfigSerializer
    from core.firewall_v2_publish import document_bytes, drafts_from_policies
    from core.models import FirewallConfig

    payloads = {
        "every_builtin": dict(_PROBE),
        "never_saved": dict(FirewallConfigSerializer(FirewallConfig()).data),
    }
    out: dict[str, str] = {}
    for name, payload in payloads.items():
        rows, _unsupported = drafts_from_policies(_PROBE_SLUG, payload, mode, [])
        out[name] = document_bytes(
            _PROBE_SLUG, payload, rows, _PROBE_SLUG, controls_enabled=True, rules_mode=mode,
        ).decode("utf-8")
    return out


def fingerprint() -> str:
    """sha256 identifying what the running publisher writes (see the module docstring)."""
    from core.firewall_v2_controls import new_rules_mode

    blob = json.dumps(
        {
            "code": code_digest(),
            "migrations": migrations_digest(),
            "settings": _settings(),
            "documents": _documents(new_rules_mode()),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stale_orgs(current: str, skip: Collection[int] = ()) -> Any:
    """Active orgs with no document, or whose document is not stamped by ``current`` at its
    current revision (another publisher built it, or an older image changed it after a
    rollback: it raises ``revision`` but does not know the stamp)."""
    from auth.models import Organization
    from django.db.models import F

    orgs = Organization.objects.filter(is_active=True).exclude(
        v2_plan_document__publisher=current,
        v2_plan_document__publisher_revision=F("v2_plan_document__revision"),
    )
    if skip:
        orgs = orgs.exclude(pk__in=list(skip))
    return orgs


def unapplied_migrations() -> list[str]:
    """Migrations of :data:`MIGRATION_APPS` (and their dependencies) not applied yet.

    A container can start before ``deploy-ec2.sh`` runs ``migrate``; sweeping then would build
    documents from rows a pending data migration is about to change.
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    targets = [node for node in executor.loader.graph.leaf_nodes() if node[0] in MIGRATION_APPS]
    return [f"{m.app_label}.{m.name}" for m, _backwards in executor.migration_plan(targets)]


def sweep(
    current: str,
    *,
    lock: Any = None,
    skip: Collection[int] = (),
    force: bool = False,
) -> dict[str, Any]:
    """Republish the stale active orgs (every active org with ``force``), :data:`BATCH` per query.

    Returns ``published``, ``failed_orgs`` (ids) and ``stopped`` (``""``, ``consecutive_failures``
    or ``lost_lock``). The lock, when given, is refreshed every :data:`LOCK_REFRESH_S`.
    """
    from auth.models import Organization
    from core.firewall_v2_publish import publish_for_org
    from redis.exceptions import LockError

    published = streak = last = 0
    failed: list[int] = []
    started = refreshed = reported = time.monotonic()

    def result(stopped: str = "") -> dict[str, Any]:
        return {"published": published, "failed_orgs": failed, "stopped": stopped}

    while True:
        if force:
            base = Organization.objects.filter(is_active=True).exclude(pk__in=list(skip))
        else:
            base = stale_orgs(current, skip)
        batch = list(base.filter(pk__gt=last).order_by("pk").only("pk", "slug")[:BATCH])
        if not batch:
            return result()
        for org in batch:
            now = time.monotonic()
            if lock is not None and now - refreshed >= LOCK_REFRESH_S:
                try:
                    lock.extend(LOCK_TTL_S, replace_ttl=True)
                except LockError:
                    logger.warning("gateway-v2 plan rollout lost its lock; stopping this sweep")
                    return result("lost_lock")
                refreshed = now
            if now - reported >= PROGRESS_EVERY_S:
                logger.info(
                    "gateway-v2 plan rollout progress: published=%d failed=%d last_org=%d "
                    "seconds=%.0f", published, len(failed), last, now - started,
                )
                reported = now
            last = org.pk
            if publish_for_org(org, publisher=current, notes=False).get("status") == "published":
                published += 1
                streak = 0
                continue
            failed.append(org.pk)
            streak += 1
            if streak >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "gateway-v2 plan rollout stopped after %d consecutive failures", streak
                )
                return result("consecutive_failures")


def reconcile(
    *, client: Any = None, force: bool = False, skip: Collection[int] = ()
) -> dict[str, object]:
    """Republish the stale active orgs once. Idempotent.

    ``force`` republishes every active org (the stamps are ignored). ``skip`` leaves these org ids
    alone. Statuses: ``current`` (nothing stale), ``waiting_for_migrations``, ``locked`` (another
    runner holds the lock), ``swept`` (every org it tried was published), ``incomplete`` (some
    failed) or ``lost_lock`` (stopped; the next run resumes).
    """
    current = fingerprint()
    if not force and not stale_orgs(current, skip).exists():
        return {"status": "current", "fingerprint": current}
    pending = unapplied_migrations()
    if pending:
        return {
            "status": "waiting_for_migrations",
            "fingerprint": current,
            "pending": len(pending),
            "first_pending": pending[0],
        }
    from redis.exceptions import LockError

    redis = client if client is not None else _redis()
    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_S, blocking=False, thread_local=False)
    if not lock.acquire(blocking=False):
        return {"status": "locked", "fingerprint": current}
    started = time.monotonic()
    try:
        todo = stale_orgs(current, skip).count() if not force else None
        logger.info(
            "gateway-v2 plan rollout started: fingerprint=%s stale_orgs=%s", current[:16], todo,
        )
        done = sweep(current, lock=lock, skip=skip, force=force)
    finally:
        try:
            lock.release()
        except LockError:
            logger.warning("gateway-v2 plan rollout lock was no longer ours at release")
    failed = done["failed_orgs"]
    status = "swept"
    if done["stopped"] == "lost_lock":
        status = "lost_lock"
    elif failed or done["stopped"]:
        status = "incomplete"
    note: dict[str, object] = {
        "status": status,
        "fingerprint": current,
        "published": done["published"],
        "failed": len(failed),
        "failed_orgs": failed,
        "remaining": stale_orgs(current).count(),
        "seconds": round(time.monotonic() - started, 3),
    }
    logger.info("gateway-v2 plan rollout: %s", {**note, "failed_orgs": failed[:20]})
    return note


def _drop_broken_connections() -> None:
    """``close_old_connections`` for a long-lived command: after the database went away, the next
    attempt reconnects instead of failing on the dead connection. Never inside a transaction."""
    from django.db import connections

    for conn in connections.all(initialized_only=True):
        if not conn.in_atomic_block:
            conn.close_if_unusable_or_obsolete()


def reconcile_until_settled(
    *,
    attempts: int,
    wait_s: float,
    client: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """:func:`reconcile` until it settles, at most ``attempts`` times.

    Retries cover a container that starts before ``migrate`` ran, a database or Redis that is not
    up yet, a runner elsewhere that holds the lock, and transient per-org failures. Each retry
    republishes only what is still stale. An org that failed :data:`ORG_MAX_TRIES` times is left
    for the next start: the result is then ``partial`` with those org ids in ``failed_orgs``.
    """
    tries: Counter[int] = Counter()
    note: dict[str, object] = {"status": "not_run"}
    for attempt in range(1, max(attempts, 1) + 1):
        skip = frozenset(pk for pk, count in tries.items() if count >= ORG_MAX_TRIES)
        try:
            _drop_broken_connections()
            note = reconcile(client=client, skip=skip)
        except Exception:
            logger.warning("gateway-v2 plan rollout attempt %d failed", attempt, exc_info=True)
            note = {"status": "error"}
        failed = note.get("failed_orgs")
        tries.update(failed if isinstance(failed, list) else ())
        if note.get("status") in SETTLED:
            if not skip:
                return note
            left = sorted(
                stale_orgs(str(note["fingerprint"])).filter(pk__in=list(skip))
                .values_list("pk", flat=True)
            )
            if not left:
                return note
            logger.error(
                "gateway-v2 plan rollout settled without %d org(s) that failed %d times; they "
                "keep their previous document until the next start: %s",
                len(left), ORG_MAX_TRIES, left[:50],
            )
            return {**note, "status": "partial", "failed": len(left), "failed_orgs": left}
        if attempt < attempts:
            sleep(wait_s)
    return note


def status() -> dict[str, object]:
    """How many active orgs still carry a document another publisher built (``--status``)."""
    from auth.models import Organization

    current = fingerprint()
    stale = stale_orgs(current)
    return {
        "fingerprint": current,
        "active_orgs": Organization.objects.filter(is_active=True).count(),
        "stale_orgs": stale.count(),
        "stale_sample": list(stale.order_by("pk").values_list("pk", flat=True)[:20]),
    }
