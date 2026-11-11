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

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gateway_v2.domain.incident import IsolationOrigin, isolation_scope
from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    StateKind,
    StateOp,
    Version,
)
from gateway_v2.runtime.state_sig import body_hash, canonical_body, make_manifest, make_record
from state_control.db import CommitUnknown, ControlDB, ControlTx, KindCounters, advance
from state_control.incident_scopes import isolation_body
from state_control.publisher import StatePublisher

LOG = logging.getLogger("amf.state.writer")

OK = "ok"
OK_PUBLISH_PENDING = "ok_publish_pending"
UNCHANGED = "unchanged"
UNKNOWN = "unknown"
ERROR = "error"


class LeaseLost(Exception):
    """A fenced write found its holder's lease gone. Nothing was committed."""


class RecordExists(Exception):
    """`put_many(require_new=True)` met a key that already has a record. Nothing was committed."""


@dataclass(frozen=True, slots=True)
class Fence:
    """Commit only while `holder` holds the projector lease; each fenced write renews it."""

    holder: str
    ttl_ms: int


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
        return self.status in (OK, OK_PUBLISH_PENDING, UNCHANGED)

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
        if_changed: bool = False,
        fence: Fence | None = None,
    ) -> WriteOutcome:
        """Commit one record, then publish exactly that record.

        `if_changed` skips the write, inside the counter lock, when the stored record already has
        this body, this `deleted` flag and (for a kill switch) this engaged flag: the outcome is
        `unchanged` and nothing is bumped or published. `fence` makes the commit conditional on
        the projector lease (raises `LeaseLost`).
        """
        record, counters = self._commit(
            kind,
            key,
            body,
            deleted=deleted,
            op=op,
            engaged=engaged,
            epoch_bump=epoch_bump,
            if_changed=if_changed,
            fence=fence,
        )
        if record is None:
            return WriteOutcome(UNCHANGED, kind, counters.version, counters.feed_seq, 0)
        manifest = self._manifest(kind, counters)
        return self._publish_one(record, manifest, counters, engaged)

    def put_many(
        self,
        kind: StateKind,
        entries: Sequence[tuple[str, Mapping[str, object]]],
        *,
        engaged: Mapping[str, bool] | None = None,
        require_new: bool = False,
        fence: Fence | None = None,
    ) -> WriteOutcome:
        """Bulk onboarding. ONE transaction, ONE version bump, ONE manifest, ONE nudge.

        `require_new` refuses (raises `RecordExists`, nothing committed) when any key already has
        a record: a batch publish writes records before the manifest outside one MULTI, which is
        harmless for new keys (their index scores sit above the attested manifest) and breaks a
        mid-round reader's count for an existing one.
        """
        if not entries:
            counters = self._db.counters(kind)
            return WriteOutcome(OK, kind, counters.version, counters.feed_seq, 0)
        records, counters = self._commit_many(
            kind, entries, engaged, require_new=require_new, fence=fence,
        )
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

    def budget_set(self, org_id: str, pool_tokens: int) -> WriteOutcome:
        """Write (or leave unchanged) the signed per-org token budget.

        `if_changed` skips the write when the stored body already carries this pool, so an
        unchanged budget bumps nothing (the generation, which IS the record's `feed_seq`, only
        advances on a changed body). The body is a numeric token count only, never a credential.
        """
        return self.put(
            StateKind.BUDGET,
            org_id,
            {"org_id": org_id, "pool_tokens": pool_tokens},
            if_changed=True,
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

    def isolation(
        self,
        model: str,
        *,
        on: bool,
        fallback: str | None = None,
        origin: IsolationOrigin = IsolationOrigin.OPERATOR,
        reason: str = "",
        expires_at: float | None = None,
        created_at: float | None = None,
        no_expiry: bool = False,
        now: float | None = None,
    ) -> WriteOutcome:
        """Write (or clear) the signed `iso:<model>` record through the same `put` as a kill switch.

        `off` is an explicit record, as for `killswitch`. A model that does not canonicalise, or a
        body the gateway would read as malformed, raises `ValueError` and writes NOTHING (GW29
        design 4.6, 7). IDEMPOTENT for `origin=auto`: while an auto isolation of this model is
        already active, a second `on` returns `unchanged` and writes and publishes nothing, so N
        gateways that all detected the same incident produce exactly one record (AC27).
        """
        scope = isolation_scope(model)
        body = isolation_body(
            on=on, fallback=fallback, origin=origin, reason=reason, expires_at=expires_at,
            created_at=created_at, no_expiry=no_expiry,
        )
        if on and origin is IsolationOrigin.AUTO and self._auto_isolation_active(
            scope, time.time() if now is None else now,
        ):
            counters = self._db.counters(StateKind.KS)
            return WriteOutcome(UNCHANGED, StateKind.KS, counters.version, counters.feed_seq, 0)
        return self.put(StateKind.KS, scope, body, engaged=on)

    def _auto_isolation_active(self, scope: str, now: float) -> bool:
        with self._db.tx() as tx:
            record = tx.record(StateKind.KS, scope)
            engaged = tx.engaged(StateKind.KS, scope)
        if record is None or record.deleted or not engaged:
            return False
        try:
            body = json.loads(record.body)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(body, dict) or body.get("origin") != IsolationOrigin.AUTO.value:
            return False
        expires = body.get("expires_at")
        if isinstance(expires, bool) or not isinstance(expires, int | float):
            return False  # no usable deadline: write a fresh, bounded record (tightening only)
        return float(expires) > now

    def repair_epoch(
        self,
        kind: StateKind,
        *,
        store_version: Version | None = None,
        store_feed_seq: int = 0,
    ) -> KindCounters:
        """Move the kind above whatever the STORE holds that Postgres never issued.

        A divergence (a restore from a stray backup, a rogue publisher) can leave the store
        ahead. A gateway that applied the store's position refuses any manifest older in VERSION
        or lower in FEED_SEQ (`decode_manifest`), whatever its epoch. So every record is re-signed,
        byte-for-byte and with its `deleted`, `op` and engaged flag kept, at a new epoch above both
        sides and at fresh positions above both sides. The manifest then sits exactly on the
        highest record, so the next round finds nothing to repair (the old loop: an epoch bump
        alone left the manifest one position above every record, forever).

        An EMPTY kind cannot be lifted above a reader's position, because a reader refuses count
        0 at any position but 0. It gets the epoch bump at position 0, which a cold reader accepts;
        a warm reader that applied the higher position stays fail-closed until it restarts.
        """
        with self._db.tx() as tx:
            counters = tx.counters(kind, lock=True)
            epoch = max(counters.version.epoch, store_version.epoch if store_version else 0) + 1
            records = sorted(tx.records(kind), key=lambda record: record.feed_seq)
            if not records:
                moved = KindCounters(
                    version=Version(epoch, 0),
                    feed_seq=counters.feed_seq,
                    count=counters.count,
                    on_count=counters.on_count,
                )
            else:
                base = max(counters.feed_seq, store_feed_seq)
                for index, record in enumerate(records, start=1):
                    tx.upsert(
                        make_record(
                            self._secret,
                            kind,
                            record.key,
                            _body_of(record),
                            Version(epoch, index),
                            base + index,
                            deleted=record.deleted,
                            op=record.op,
                        ),
                        engaged=tx.engaged(kind, record.key),
                        actor=self._actor,
                    )
                moved = KindCounters(
                    version=Version(epoch, len(records)),
                    feed_seq=base + len(records),
                    count=counters.count,
                    on_count=counters.on_count,
                )
            tx.set_counters(kind, moved)
        LOG.warning(
            "state epoch repaired kind=%s version=%s feed_seq=%d records=%d",
            kind.value,
            moved.version,
            moved.feed_seq,
            len(records),
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
        if_changed: bool,
        fence: Fence | None,
    ) -> tuple[SignedRecord | None, KindCounters]:
        engage = False if deleted else bool(engaged)
        try:
            with self._db.tx() as tx:
                counters = tx.counters(kind, lock=True)
                _check_fence(tx, fence)
                previous = tx.record(kind, key)
                was_engaged = tx.engaged(kind, key)
                if (
                    if_changed
                    and previous is not None
                    and previous.deleted == deleted
                    and previous.content_hash == body_hash(canonical_body(body))
                    and (engaged is None or was_engaged == engage)
                ):
                    return None, counters
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
        *,
        require_new: bool,
        fence: Fence | None,
    ) -> tuple[tuple[SignedRecord, ...], KindCounters]:
        records: list[SignedRecord] = []
        with self._db.tx() as tx:
            counters = tx.counters(kind, lock=True)
            _check_fence(tx, fence)
            moved = counters
            for key, body in entries:
                engage = False if engaged is None else bool(engaged.get(key, False))
                previous = tx.record(kind, key)
                if require_new and previous is not None:
                    raise RecordExists(f"{kind.value} record {key!r} already exists")
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


def _check_fence(tx: ControlTx, fence: Fence | None) -> None:
    if fence is not None and not tx.renew_lease(fence.holder, fence.ttl_ms):
        raise LeaseLost(f"projector lease is not held by {fence.holder!r}")


def _body_of(record: SignedRecord) -> Mapping[str, object]:
    import json

    parsed: Any = json.loads(record.body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{record.kind.value} record {record.key!r} body is not an object")
    return parsed


def _is_on(record: SignedRecord) -> bool:
    return not record.deleted and bool(_body_of(record).get("on"))
