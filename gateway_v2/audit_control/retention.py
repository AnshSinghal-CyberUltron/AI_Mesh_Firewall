"""GW27: the audit retention sweep (stamped at write time, non-retroactive).

A record carries the ``retention_days`` in force when it was written. This sweeper deletes only
rows whose OWN stamp has elapsed: a NULL stamp is never deleted, a row younger than its stamp is
never deleted, no statement runs without an ``org_id`` predicate, and nothing is deleted younger
than the hard floor whatever a stamp says. It is a periodic task inside the existing exporter
process (``python -m audit_control``), off by default (``AMF_AUDIT_RETENTION_SWEEP``).

One sweep is one bounded pass: orgs are enumerated by keyset (``org_id > after_org``), each org is
visited at most once, and at most ``max_batches_per_org`` batches run per org, so a sweep terminates
in ``dry_run`` (nothing is deleted, so a repeat-until-empty loop would never end), with persistently
locked rows (``SKIP LOCKED``) and under failing batches. On any database error nothing further is
deleted. The clock defaults to ``time.time_ns`` because ``recorded_at_ns`` is wall-clock.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from audit_control.sink_pg import BoundedDatabase
from gateway_v2.runtime.resources_org import RetentionBounds

LOG = logging.getLogger("amf.audit.retention")

MODES = ("off", "dry_run", "on")
_NS_PER_DAY = 86_400_000_000_000

# The predicate shared by the enumeration, the dry-run count and the batch delete. The coarse
# hard-floor bound is implied by the per-row bound, so it never excludes an expired row; it exists
# so the planner can bound the index scan to (org_id, recorded_at_ns < coarse).
_ELIGIBLE = """
     retention_days IS NOT NULL
     AND retention_days >= %(hard_floor_days)s
     AND recorded_at_ns < %(now_ns)s - %(hard_floor_days)s::bigint * 86400000000000
     AND recorded_at_ns < %(now_ns)s - retention_days::bigint * 86400000000000
"""

ORG_PAGE_SQL = f"""
SELECT DISTINCT org_id FROM amf_audit_record
 WHERE org_id > %(after_org)s
   AND {_ELIGIBLE}
 ORDER BY org_id
 LIMIT %(org_page)s
"""

BATCH_DELETE_SQL = f"""
DELETE FROM amf_audit_record
WHERE (org_id, stream_id) IN (
  SELECT org_id, stream_id
    FROM amf_audit_record
   WHERE org_id = %(org)s
     AND {_ELIGIBLE}
   ORDER BY recorded_at_ns
   LIMIT %(batch)s
   FOR UPDATE SKIP LOCKED
)
"""

DRY_RUN_COUNT_SQL = f"""
SELECT count(*) FROM amf_audit_record
 WHERE org_id = %(org)s
   AND {_ELIGIBLE}
"""


def parse_mode(raw: str | None) -> str:
    """``off|dry_run|on``; anything else is ``off`` (fail toward not deleting)."""
    value = (raw or "").strip().lower()
    if not value:
        return "off"
    if value in MODES:
        return value
    LOG.warning(
        "AMF_AUDIT_RETENTION_SWEEP=%r is not one of off|dry_run|on: treating it as off", raw
    )
    return "off"


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one sweep did. ``deleted`` is the would-delete count in ``dry_run``."""

    orgs: int
    deleted: int
    dry_run: bool
    failed: bool


class RetentionSweeper:
    """Deletes (or counts, in dry-run) audit rows whose own stamped retention has elapsed."""

    def __init__(
        self,
        database: BoundedDatabase,
        bounds: RetentionBounds,
        *,
        mode: str,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if mode not in ("dry_run", "on"):
            raise ValueError(f"a sweeper needs mode dry_run or on, got {mode!r}")
        self._db = database
        self._bounds = bounds
        self._dry_run = mode == "dry_run"
        self._clock_ns = clock_ns

    @property
    def period_s(self) -> float:
        return self._bounds.sweep_period_s

    def sweep_once(self) -> SweepReport:
        """One bounded pass over the orgs that have eligible rows. Never raises."""
        now_ns = self._clock_ns()
        orgs = deleted = 0
        after_org = ""
        try:
            while True:
                page = self._org_page(after_org, now_ns)
                if not page:
                    break
                for org in page:
                    orgs += 1
                    deleted += self._sweep_org(org, now_ns)
                after_org = page[-1]
        except Exception as exc:  # a failing database stops the sweep; nothing more is deleted
            LOG.warning(
                "audit_retention_sweep_failed error=%s (deleted nothing further)",
                f"{type(exc).__name__}: {exc}"[:300],
            )
            return SweepReport(orgs, deleted, self._dry_run, failed=True)
        return SweepReport(orgs, deleted, self._dry_run, failed=False)

    def _params(self, now_ns: int, **extra: object) -> dict[str, Any]:
        return {"now_ns": now_ns, "hard_floor_days": self._bounds.hard_floor_days, **extra}

    def _org_page(self, after_org: str, now_ns: int) -> list[str]:
        params = self._params(now_ns, after_org=after_org, org_page=self._bounds.org_page)
        with self._db.cursor_tx() as cursor:
            cursor.execute(ORG_PAGE_SQL, params)
            return [str(row[0]) for row in cursor.fetchall()]

    def _sweep_org(self, org: str, now_ns: int) -> int:
        if self._dry_run:
            with self._db.cursor_tx() as cursor:
                cursor.execute(DRY_RUN_COUNT_SQL, self._params(now_ns, org=org))
                row = cursor.fetchone()
            would = 0 if row is None else int(row[0])
            LOG.info("would_delete=%d org=%s", would, org)
            return would
        total = 0
        for _ in range(self._bounds.max_batches_per_org):
            params = self._params(now_ns, org=org, batch=self._bounds.sweep_batch)
            with self._db.cursor_tx() as cursor:
                cursor.execute(BATCH_DELETE_SQL, params)
                removed = int(getattr(cursor, "rowcount", 0) or 0)
            total += removed
            if removed < self._bounds.sweep_batch:
                break
        return total
