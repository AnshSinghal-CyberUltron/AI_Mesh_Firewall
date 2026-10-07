"""The one write path: commit to Postgres, then publish ONLY what changed.

Every write reports one honest outcome, kept verbatim from the RC2 design that had 0 violations
in 15 fault runs (R2-17):

    ok                  committed and published
    ok_publish_pending  committed; the publish failed, the re-hydrator will publish it
    unknown             the connection failed DURING COMMIT; the write may be durable
    error               nothing committed (the exception propagates; nothing to undo)

What GW05c changes is only the second step. `publish_record` writes five keys instead of the
kind's complete record set, so a write costs the same at 3 tenants and at 25,000.

Bulk onboarding (`put_many`) exists because the per-record path is the wrong shape for 25,000
records: it would issue 25,000 publishes, each bumping the manifest, so a worker would see 25,000
separate generations. One transaction, one version bump, one manifest, one nudge — and the reader
side absorbs it over several bounded rounds.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    StateKind,
    StateOp,
    Version,
)
from gateway_v2.runtime.state_sig import make_manifest, make_record
from state_control.db import CommitUnknown, ControlDB, KindCounters, advance
from state_control.publisher import StatePublisher

LOG = logging.getLogger("amf.state.writer")

OK = "ok"
OK_PUBLISH_PENDING = "ok_publish_pending"
UNKNOWN = "unknown"
ERROR = "error"


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """What one write did, and how much of it the store knows about."""

    status: str
    kind: StateKind
    version: Version
    feed_seq: int
    records: int

    @property
    def durable(self) -> bool:
        return self.status in (OK, OK_PUBLISH_PENDING)

    @property
    def published(self) -> bool:
        return self.status == OK


class StateWriter:
    """Commits a record and publishes it. The only component that signs state."""

    def __init__(
        self,
        db: ControlDB,
        publisher: StatePublisher,
        secret: bytes,
        *,
        actor: str = "amf.state.control",
        chunk: int = 500,
    ) -> None:
        if not secret:
            raise ValueError("a state signing secret is required")
        self._db = db
        self._publisher = publisher
        self._secret = secret
        self._actor = actor
        self._chunk = chunk

    # --- the one write path --------------------------------------------------------------------

    def put(
        self,
        kind: StateKind,
        key: str,
        body: Mapping[str, object],
        *,
        deleted: bool = False,
        op: StateOp = StateOp.PUT,
        engaged: bool | None = None,
        epoch_bump: bool = False,
    ) -> WriteOutcome:
        """Commit one record, then publish exactly that record."""
        record, counters = self._commit(
            kind, key, body, deleted=deleted, op=op, engaged=engaged, epoch_bump=epoch_bump,
        )
        manifest = self._manifest(kind, counters)
        return self._publish_one(record, manifest, counters, engaged)

    def put_many(
        self,
        kind: StateKind,
        entries: Sequence[tuple[str, Mapping[str, object]]],
        *,
        engaged: Mapping[str, bool] | None = None,
    ) -> WriteOutcome:
        """Bulk onboarding. ONE transaction, ONE version bump, ONE manifest, ONE nudge."""
        if not entries:
            counters = self._db.counters(kind)
            return WriteOutcome(OK, kind, counters.version, counters.feed_seq, 0)
        records, counters = self._commit_many(kind, entries, engaged)
        manifest = self._manifest(kind, counters)
        try:
            published = self._publisher.publish_batch(
                records, manifest, engaged=engaged, chunk=self._chunk,
            )
        except Exception as exc:  # noqa: BLE001 - store, OS and driver errors alike
            LOG.warning(
                "state batch publish failed, durable in postgres, re-hydrator will publish: %s",
                exc,
            )
            return WriteOutcome(
                OK_PUBLISH_PENDING, kind, counters.version, counters.feed_seq, len(records),
            )
        status = OK if published else OK_PUBLISH_PENDING
        return WriteOutcome(status, kind, counters.version, counters.feed_seq, len(records))

    # --- domain operations ---------------------------------------------------------------------

    def plan_set(self, org_id: str, body: Mapping[str, object]) -> WriteOutcome:
        return self.put(StateKind.PLAN, org_id, body)

    def plan_offboard(self, org_id: str) -> WriteOutcome:
        return self.put(
            StateKind.PLAN,
            org_id,
            {"org_id": org_id},
            deleted=True,
            op=StateOp.OFFBOARD,
        )

    def key_add(
        self,
        key_hash: str,
        org_id: str,
        *,
        key_id: str,
        rate_per_s: float,
        burst: float,
    ) -> WriteOutcome:
        return self.put(
            StateKind.KEY,
            key_hash,
            {
                "key_id": key_id,
                "org_id": org_id,
                "rate_per_s": rate_per_s,
                "burst": burst,
            },
        )

    def key_revoke(self, key_hash: str) -> WriteOutcome:
        """An explicit OFF record. Absence must never be the thing that means 'revoked'."""
        with self._db.tx() as tx:
            current = tx.record(StateKind.KEY, key_hash)
        body: Mapping[str, object] = (
            {"key_id": "unknown", "org_id": "unknown", "rate_per_s": 0.0, "burst": 0.0}
            if current is None
            else _body_of(current)
        )
        return self.put(
            StateKind.KEY, key_hash, body, deleted=True, op=StateOp.REVOKE,
        )

    def killswitch(self, scope: str, *, on: bool) -> WriteOutcome:
        """`off` is an explicit record too, and it is what keeps `on_count` honest."""
        return self.put(StateKind.KS, scope, {"on": on}, engaged=on)

    def repair_epoch(self, kind: StateKind) -> KindCounters:
        """Move Postgres to a new epoch when the STORE holds a version it never issued.

        A divergence (a restore from a stray backup, a rogue publisher) can leave the store
        ahead. Republishing the current content at the current version would be a REGRESS from
        the point of view of a gateway that already applied the higher one, and C36 refuses a
        regress by design, so the fleet would reject the repair. Bumping the epoch first makes
        the republished state unambiguously newer than anything any process has applied.
        """
        with self._db.tx() as tx:
            counters = tx.counters(kind, lock=True)
            moved = advance(counters, epoch_bump=True, is_new=False, engaged_delta=0)
            tx.set_counters(kind, moved)
        LOG.warning(
            "state epoch repaired kind=%s version=%s", kind.value, moved.version,
        )
        return moved

    def manifest_for(self, kind: StateKind, counters: KindCounters) -> Manifest:
        """Sign a manifest for counters read elsewhere. The re-hydrator's repair path."""
        return self._manifest(kind, counters)

    def rollback(self, log_id: int) -> WriteOutcome:
        """Restore one logged write as a NEW signed version at a new epoch. Never a regress."""
        with self._db.tx() as tx:
            entry = tx.logged(log_id)
        if entry is None:
            raise KeyError(f"no logged write {log_id}")
        record = entry.record
        return self.put(
            record.kind,
            record.key,
            _body_of(record),
            deleted=record.deleted,
            op=StateOp.ROLLBACK,
            engaged=None if record.kind is not StateKind.KS else _is_on(record),
            epoch_bump=True,
        )

    # --- internals -----------------------------------------------------------------------------

    def _commit(
        self,
        kind: StateKind,
        key: str,
        body: Mapping[str, object],
        *,
        deleted: bool,
        op: StateOp,
        engaged: bool | None,
        epoch_bump: bool,
    ) -> tuple[SignedRecord, KindCounters]:
        engage = False if deleted else bool(engaged)
        try:
            with self._db.tx() as tx:
                counters = tx.counters(kind, lock=True)
                previous = tx.record(kind, key)
                was_engaged = tx.engaged(kind, key)
                moved = advance(
                    counters,
                    epoch_bump=epoch_bump,
                    is_new=previous is None,
                    engaged_delta=(
                        0 if engaged is None else int(engage) - int(was_engaged)
                    ),
                )
                record = make_record(
                    self._secret,
                    kind,
                    key,
                    body,
                    moved.version,
                    moved.feed_seq,
                    deleted=deleted,
                    op=op,
                )
                tx.upsert(record, engaged=engage, actor=self._actor)
                tx.set_counters(kind, moved)
        except CommitUnknown:
            LOG.error("state write commit acknowledgement lost for %s/%s", kind.value, key)
            raise
        return record, moved

    def _commit_many(
        self,
        kind: StateKind,
        entries: Sequence[tuple[str, Mapping[str, object]]],
        engaged: Mapping[str, bool] | None,
    ) -> tuple[tuple[SignedRecord, ...], KindCounters]:
        records: list[SignedRecord] = []
        with self._db.tx() as tx:
            counters = tx.counters(kind, lock=True)
            moved = counters
            for key, body in entries:
                engage = False if engaged is None else bool(engaged.get(key, False))
                previous = tx.record(kind, key)
                was_engaged = tx.engaged(kind, key)
                moved = advance(
                    moved,
                    epoch_bump=False,
                    is_new=previous is None,
                    engaged_delta=(
                        0 if engaged is None else int(engage) - int(was_engaged)
                    ),
                )
                record = make_record(
                    self._secret,
                    kind,
                    key,
                    body,
                    moved.version,
                    moved.feed_seq,
                )
                tx.upsert(record, engaged=engage, actor=self._actor)
                records.append(record)
            tx.set_counters(kind, moved)
        return tuple(records), moved

    def _manifest(self, kind: StateKind, counters: KindCounters) -> Manifest:
        return make_manifest(
            self._secret,
            kind,
            counters.version,
            counters.feed_seq,
            counters.count,
            counters.on_count,
        )

    def _publish_one(
        self,
        record: SignedRecord,
        manifest: Manifest,
        counters: KindCounters,
        engaged: bool | None,
    ) -> WriteOutcome:
        flag = None if engaged is None else (False if record.deleted else engaged)
        try:
            published = self._publisher.publish_record(record, manifest, engaged=flag)
        except Exception as exc:  # noqa: BLE001 - after a commit NOTHING may report a failure
            LOG.warning(
                "state publish failed, durable in postgres, re-hydrator will publish: %s", exc,
            )
            return WriteOutcome(
                OK_PUBLISH_PENDING, record.kind, counters.version, counters.feed_seq, 1,
            )
        status = OK if published else OK_PUBLISH_PENDING
        return WriteOutcome(status, record.kind, counters.version, counters.feed_seq, 1)


def _body_of(record: SignedRecord) -> Mapping[str, object]:
    import json

    parsed: Any = json.loads(record.body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{record.kind.value} record {record.key!r} body is not an object")
    return parsed


def _is_on(record: SignedRecord) -> bool:
    return not record.deleted and bool(_body_of(record).get("on"))
