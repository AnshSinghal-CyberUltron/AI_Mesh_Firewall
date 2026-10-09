"""The Postgres source of truth. One short-lived connection per operation.

Every session sets four timeouts. They belong to GW05b (R2-04: one idle writer transaction
holding `FOR UPDATE` stalled re-hydration for 38 s, and two re-hydrators blocked on the same
lock), and they are set here because that is where a connection is opened — a bound that only
some connections carry is not a bound.

They are applied TWICE, and the duplication is the point. The libpq `options` startup parameter
is belt-and-braces: it works on a direct connection and is SILENTLY DISCARDED by PgBouncer, whose
`IGNORE_STARTUP_PARAMETERS` includes `options` (docker-compose.yml, deploy/pgbouncer/pgbouncer.ini)
— and every application in this stack is required to reach Postgres through `pgbouncer:6432`. So
`SET LOCAL`, issued as the first statements of every transaction, is AUTHORITATIVE: it survives
transaction pooling and `DISCARD ALL` because it is scoped to the transaction the pooler is
currently pinning to a server connection. The control plane already learned this for the analytics
alias; see `control/ai_mesh_control/main_app/analytics_db.py`, whose docstring says the same thing.

`tcp_user_timeout` is additionally set as a libpq CONNECTION parameter, not only as a GUC. The GUC
bounds the SERVER's socket; the stall this must cover is the re-hydrator waiting on a reply from a
host that has gone away, which is the CLIENT's socket. TCP keepalives go with it, because
`tcp_user_timeout` only bounds data awaiting acknowledgement and an idle connection has none — the
keepalive probes are what create the unacknowledged data the timeout then bounds.

`verify_bounds()` reads all four back out of `pg_settings` and refuses to start when any of them
did not apply. Nothing verified the bounds before, which is exactly why the PgBouncer drop was
invisible: the settings were constructed, discarded, and never missed.

`snapshot` reads `REPEATABLE READ, READ ONLY` and takes **no** row lock. RC2 read the version row
`FOR SHARE`, which waits behind a writer holding it `FOR UPDATE`; that is exactly how a single
idle transaction stalled re-hydration indefinitely. A repeatable-read snapshot sees one
consistent generation without waiting for anybody, which is all a republish needs.

Counters live in one row per kind and are updated inside the write transaction. That is what
keeps a publish O(1): `count` and `on_count` are maintained incrementally rather than recomputed
by scanning the kind, which is the R2-02 defect.

THE DSN MUST POINT AT THE PRIMARY. A read replica is forbidden here, for the re-hydrator as much
as for the writer, and the reason is not performance.

GW05b's freshness stamp is an assertion about durable truth: "at this moment the store held at
least these versions, and they cover every write committed before it". A re-hydrator reading a
replica would issue that assertion against a view that is itself behind the writer, and every
gateway in the fleet would then trust it -- which is R2-03's SP1 reproduced one tier up, by the
component built to prevent it. The gateway data plane never reads Postgres at all (grep: no
psycopg import anywhere under `gateway_v2/`), so there is no read traffic here to offload and no
latency argument to weigh against that.

If a future change wants a replica for cost or for analytics, it needs a separate connection and
a separate class. Do not add a replica DSN to this one.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Sequence
from typing import Any

from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, Version
from state_control.db import ZERO_COUNTERS, CommitUnknown, KindCounters, LoggedWrite
from state_control.schema import SCHEMA

LOG = logging.getLogger("amf.state.pg")

_COLUMNS = (
    "kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body"
)
_PLACEHOLDERS = ", ".join(["%s"] * 10)

BOUND_NAMES = (
    "lock_timeout",
    "statement_timeout",
    "idle_in_transaction_session_timeout",
    "tcp_user_timeout",
)
"""The four R2-04 session bounds, in the order the runbook names them. All units are ms."""


class ControlPlaneBoundsNotApplied(RuntimeError):
    """A declared session bound did not reach the server. Fatal at start-up, never ignored.

    The one failure mode this class exists for: a pooler that drops the `options` startup
    parameter, so every bound is constructed and none is in force. R2-04's whole remedy is that
    the four timeouts hold on EVERY control-plane connection, and an unverified bound is
    indistinguishable from an absent one.
    """


def _rejects_option(exc: BaseException) -> bool:
    """Did libpq refuse a connection parameter, rather than fail to connect?

    `tcp_user_timeout` and the keepalive parameters need libpq >= 12 and are Linux-only, so a
    build without them must degrade to the GUC rather than refuse to open a connection at all.
    """
    text = str(exc).lower()
    return "invalid connection option" in text or "unrecognized" in text


def _record(row: tuple[Any, ...]) -> SignedRecord:
    kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body = row
    return SignedRecord(
        kind=StateKind(kind),
        key=key,
        version=Version(int(epoch), int(seq)),
        feed_seq=int(feed_seq),
        content_hash=content_hash,
        deleted=bool(deleted),
        op=StateOp(op),
        signature=signature,
        body=body.encode("utf-8") if isinstance(body, str) else bytes(body),
    )


def _values(record: SignedRecord) -> tuple[Any, ...]:
    return (
        record.kind.value,
        record.key,
        record.version.epoch,
        record.version.seq,
        record.feed_seq,
        record.content_hash,
        record.deleted,
        record.op.value,
        record.signature,
        record.body.decode("utf-8"),
    )


class PostgresTx:
    """One transaction. Satisfies `state_control.db.ControlTx`."""

    def __init__(self, cursor: Any) -> None:
        self._cur = cursor

    def counters(self, kind: StateKind, *, lock: bool) -> KindCounters:
        self._cur.execute(
            "INSERT INTO amf_state_counter (kind, epoch, seq, feed_seq, count, on_count) "
            "VALUES (%s, %s, %s, 0, 0, 0) ON CONFLICT DO NOTHING",
            (kind.value, ZERO_COUNTERS.version.epoch, ZERO_COUNTERS.version.seq),
        )
        self._cur.execute(
            "SELECT epoch, seq, feed_seq, count, on_count FROM amf_state_counter "
            "WHERE kind = %s" + (" FOR UPDATE" if lock else ""),
            (kind.value,),
        )
        epoch, seq, feed_seq, count, on_count = self._cur.fetchone()
        return KindCounters(
            version=Version(int(epoch), int(seq)),
            feed_seq=int(feed_seq),
            count=int(count),
            on_count=int(on_count),
        )

    def set_counters(self, kind: StateKind, counters: KindCounters) -> None:
        self._cur.execute(
            "UPDATE amf_state_counter SET epoch = %s, seq = %s, feed_seq = %s, "
            "count = %s, on_count = %s WHERE kind = %s",
            (
                counters.version.epoch,
                counters.version.seq,
                counters.feed_seq,
                counters.count,
                counters.on_count,
                kind.value,
            ),
        )

    def record(self, kind: StateKind, key: str) -> SignedRecord | None:
        self._cur.execute(
            f"SELECT {_COLUMNS} FROM amf_state_record WHERE kind = %s AND key = %s",
            (kind.value, key),
        )
        row = self._cur.fetchone()
        return None if row is None else _record(row)

    def engaged(self, kind: StateKind, key: str) -> bool:
        self._cur.execute(
            "SELECT engaged FROM amf_state_record WHERE kind = %s AND key = %s",
            (kind.value, key),
        )
        row = self._cur.fetchone()
        return bool(row[0]) if row is not None else False

    def upsert(self, record: SignedRecord, *, engaged: bool, actor: str) -> None:
        values = _values(record)
        self._cur.execute(
            f"INSERT INTO amf_state_record ({_COLUMNS}, engaged) "
            f"VALUES ({_PLACEHOLDERS}, %s) "
            "ON CONFLICT (kind, key) DO UPDATE SET epoch = EXCLUDED.epoch, "
            "seq = EXCLUDED.seq, feed_seq = EXCLUDED.feed_seq, "
            "content_hash = EXCLUDED.content_hash, deleted = EXCLUDED.deleted, "
            "op = EXCLUDED.op, signature = EXCLUDED.signature, body = EXCLUDED.body, "
            "engaged = EXCLUDED.engaged, updated_at = now()",
            (*values, engaged),
        )
        self._cur.execute(
            f"INSERT INTO amf_state_log ({_COLUMNS}, actor) VALUES ({_PLACEHOLDERS}, %s)",
            (*values, actor),
        )

    def records(self, kind: StateKind) -> tuple[SignedRecord, ...]:
        self._cur.execute(
            f"SELECT {_COLUMNS} FROM amf_state_record WHERE kind = %s ORDER BY key",
            (kind.value,),
        )
        return tuple(_record(row) for row in self._cur.fetchall())

    def engaged_keys(self, kind: StateKind) -> tuple[str, ...]:
        self._cur.execute(
            "SELECT key FROM amf_state_record WHERE kind = %s AND engaged ORDER BY key",
            (kind.value,),
        )
        return tuple(row[0] for row in self._cur.fetchall())

    def logged(self, log_id: int) -> LoggedWrite | None:
        self._cur.execute(
            f"SELECT {_COLUMNS}, actor FROM amf_state_log WHERE id = %s",
            (log_id,),
        )
        row = self._cur.fetchone()
        if row is None:
            return None
        return LoggedWrite(log_id=log_id, record=_record(row[:10]), actor=row[10])

    def oldest_unpublished_at(self, kind: StateKind, above_feed_seq: int) -> float | None:
        """When the oldest committed write ABOVE the store's position was written. SP2's detector.

        No `published` flag and no migration: a write is unpublished exactly when its `feed_seq`
        is above what the store's manifest attests, which is derived from truth rather than from a
        column that can drift out of step with it. Served by the existing
        `amf_state_record (kind, feed_seq)` index.
        """
        self._cur.execute(
            "SELECT MIN(updated_at) FROM amf_state_record WHERE kind = %s AND feed_seq > %s",
            (kind.value, above_feed_seq),
        )
        row = self._cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0].timestamp())

    def settings(self, names: Sequence[str]) -> dict[str, str]:
        """What the server says the named GUCs are, as it sees them. The bound read-back."""
        self._cur.execute(
            "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
            (list(names),),
        )
        return {str(row[0]): str(row[1]) for row in self._cur.fetchall()}


class PostgresControlDB:
    """Satisfies `state_control.db.ControlDB`. Bounded sessions, lock-free snapshots."""

    def __init__(
        self,
        dsn: str,
        *,
        lock_timeout_ms: int = 2_000,
        statement_timeout_ms: int = 5_000,
        idle_tx_timeout_ms: int = 5_000,
        tcp_user_timeout_ms: int = 5_000,
        connect_timeout_s: int = 5,
        keepalives_idle_s: int = 2,
        keepalives_interval_s: int = 1,
        keepalives_count: int = 3,
    ) -> None:
        import psycopg

        self._psycopg = psycopg
        self._dsn = dsn
        self._connect_timeout_s = connect_timeout_s
        # One tuple, consumed three ways: the startup options, the SET LOCAL statements and the
        # read-back comparison. Three spellings of four numbers is how one of them drifts.
        self._bounds: tuple[tuple[str, int], ...] = (
            ("lock_timeout", int(lock_timeout_ms)),
            ("statement_timeout", int(statement_timeout_ms)),
            ("idle_in_transaction_session_timeout", int(idle_tx_timeout_ms)),
            ("tcp_user_timeout", int(tcp_user_timeout_ms)),
        )
        # Values are ints by construction, so the interpolation cannot carry anything but digits.
        # SET LOCAL takes no placeholders, which is why this is built rather than parameterised.
        self._set_local: tuple[str, ...] = tuple(
            f"SET LOCAL {name} = {value}" for name, value in self._bounds
        )
        bounds = " ".join(
            (
                f"-c lock_timeout={lock_timeout_ms}ms",
                f"-c statement_timeout={statement_timeout_ms}ms",
                f"-c idle_in_transaction_session_timeout={idle_tx_timeout_ms}ms",
                f"-c tcp_user_timeout={tcp_user_timeout_ms}",
            ),
        )
        # psycopg's `options=` keyword REPLACES any options already in the DSN, so a caller's
        # search_path (or anything else they set there) would be silently dropped. Append.
        from psycopg.conninfo import conninfo_to_dict

        existing = conninfo_to_dict(dsn).get("options") or ""
        self._options = f"{existing} {bounds}".strip() if existing else bounds
        # Client-side socket bounds. The GUC above governs the SERVER's socket; these govern
        # ours, which is the one that hangs when the server stops answering.
        self._socket: dict[str, int] = {
            "tcp_user_timeout": int(tcp_user_timeout_ms),
            "keepalives": 1,
            "keepalives_idle": int(keepalives_idle_s),
            "keepalives_interval": int(keepalives_interval_s),
            "keepalives_count": int(keepalives_count),
        }

    def _open(self, **extra: int) -> Any:
        # psycopg types `connect` with named keywords, so the libpq pass-through parameters
        # (tcp_user_timeout, keepalives*) are invisible to the checker. They are real libpq
        # options, not psycopg ones, which is why they travel as **extra.
        connect: Any = self._psycopg.connect
        return connect(
            self._dsn,
            autocommit=False,
            connect_timeout=self._connect_timeout_s,
            options=self._options,
            **extra,
        )

    def _connect(self, *, read_only: bool = False) -> Any:
        try:
            connection = self._open(**self._socket)
        except self._psycopg.Error as exc:
            if not self._socket or not _rejects_option(exc):
                raise
            # libpq is too old or not on Linux. Degrade to the server-side GUC rather than
            # refusing to open a connection at all, and say so once.
            LOG.warning(
                "libpq rejected the client socket bounds (%s); "
                "falling back to the server-side GUC only",
                exc,
            )
            self._socket = {}
            connection = self._open()
        if read_only:
            # Lock-free: a repeatable-read snapshot never waits behind a held FOR UPDATE (R2-04).
            connection.read_only = True
            connection.isolation_level = self._psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def _apply_bounds(self, cursor: Any) -> None:
        """Issue the four bounds as the FIRST statements of the transaction.

        Authoritative, because PgBouncer discards the `options` startup parameter. `SET LOCAL`
        does not take a transaction snapshot, so a REPEATABLE READ snapshot is still taken at the
        first statement that actually reads — the isolation semantics are untouched.
        """
        for statement in self._set_local:
            cursor.execute(statement)

    def verify_bounds(self) -> dict[str, str]:
        """Read all four bounds back from the server. Raises when any did not apply.

        Called once at start-up, not per connection: a per-round check costs a round trip on the
        availability path, and the failure it catches is a configuration fact that does not change
        while the process runs.
        """
        with self.tx() as tx:
            applied = tx.settings(BOUND_NAMES)
        wrong = {
            name: f"expected {value}, server reports {applied.get(name, 'absent')}"
            for name, value in self._bounds
            if applied.get(name) is None or int(applied[name]) != value
        }
        if wrong:
            raise ControlPlaneBoundsNotApplied(
                "control-plane session bounds did not apply: "
                + "; ".join(f"{name}: {why}" for name, why in sorted(wrong.items()))
                + ". A pooler that ignores the `options` startup parameter is the usual cause; "
                "SET LOCAL should have covered it, so check that this DSN reaches Postgres.",
            )
        LOG.info(
            "control-plane session bounds verified: %s",
            ", ".join(f"{name}={applied[name]}ms" for name, _ in self._bounds),
        )
        return applied

    def init_schema(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            self._apply_bounds(cursor)
            cursor.execute(SCHEMA)

    @contextlib.contextmanager
    def cursor_tx(self) -> Iterator[Any]:
        """The same bounded transaction, yielding the RAW cursor. GW14c's seam.

        The audit sink (`audit_control/`) needs the four session bounds and the commit semantics
        this class already proves, over a different schema. Exposing the cursor is the smaller of
        the two available mistakes: the alternative is a second copy of the bounds, and this
        module's own docstring says why — *"three spellings of four numbers is how one of them
        drifts"*. `verify_bounds()` therefore covers the audit sink too, which is the point.

        Callers get no state-specific helpers and no schema knowledge, so nothing about the
        control plane's tables leaks through this seam.
        """
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                self._apply_bounds(cursor)
                yield cursor
            try:
                connection.commit()
            except self._psycopg.Error as exc:
                raise CommitUnknown(f"{type(exc).__name__}: {exc}"[:300]) from exc
        except BaseException:
            with contextlib.suppress(self._psycopg.Error):
                if not connection.closed:
                    connection.rollback()
            raise
        finally:
            with contextlib.suppress(self._psycopg.Error):
                connection.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[PostgresTx]:
        """An exception in the body rolls back. A failure OF the commit is CommitUnknown."""
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                self._apply_bounds(cursor)
                yield PostgresTx(cursor)
            try:
                connection.commit()
            except self._psycopg.Error as exc:
                raise CommitUnknown(f"{type(exc).__name__}: {exc}"[:300]) from exc
        except BaseException:
            with contextlib.suppress(self._psycopg.Error):
                if not connection.closed:
                    connection.rollback()
            raise
        finally:
            connection.close()

    def counters(self, kind: StateKind) -> KindCounters:
        """The re-hydrator's per-round head read. READ ONLY, REPEATABLE READ, no locks.

        This used to go through `tx()` -- a read-write READ COMMITTED transaction whose first
        statement is `INSERT ... ON CONFLICT DO NOTHING` -- four times a second, per re-hydrator,
        on the path the fleet's availability depends on. A read path that writes relies on a
        subtle visibility rule (an `ON CONFLICT` probe does not wait on a lock-only `xmax`) to
        avoid the very stall R2-04 is about, and relying on that rather than on taking no lock at
        all is the wrong way round.

        Creating the counter row stays where it belongs: `PostgresTx.counters`, used by the
        writer, which is the only caller that needs the row to exist. An absent row reads as
        `ZERO_COUNTERS` here exactly as it does in `snapshot`.
        """
        connection = self._connect(read_only=True)
        try:
            with connection.cursor() as cursor:
                self._apply_bounds(cursor)
                cursor.execute(
                    "SELECT epoch, seq, feed_seq, count, on_count FROM amf_state_counter "
                    "WHERE kind = %s",
                    (kind.value,),
                )
                row = cursor.fetchone()
                if row is None:
                    return ZERO_COUNTERS
                return KindCounters(
                    version=Version(int(row[0]), int(row[1])),
                    feed_seq=int(row[2]),
                    count=int(row[3]),
                    on_count=int(row[4]),
                )
        finally:
            connection.rollback()
            connection.close()

    def oldest_unpublished_at(self, kind: StateKind, above_feed_seq: int) -> float | None:
        """Read-only and lock-free, like every other re-hydrator read (R2-04)."""
        connection = self._connect(read_only=True)
        try:
            with connection.cursor() as cursor:
                self._apply_bounds(cursor)
                return PostgresTx(cursor).oldest_unpublished_at(kind, above_feed_seq)
        finally:
            connection.rollback()
            connection.close()

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        """One consistent generation, read without taking a single lock."""
        connection = self._connect(read_only=True)
        try:
            with connection.cursor() as cursor:
                self._apply_bounds(cursor)
                tx = PostgresTx(cursor)
                cursor.execute(
                    "SELECT epoch, seq, feed_seq, count, on_count FROM amf_state_counter "
                    "WHERE kind = %s",
                    (kind.value,),
                )
                row = cursor.fetchone()
                counters = (
                    ZERO_COUNTERS
                    if row is None
                    else KindCounters(
                        version=Version(int(row[0]), int(row[1])),
                        feed_seq=int(row[2]),
                        count=int(row[3]),
                        on_count=int(row[4]),
                    )
                )
                return counters, tx.records(kind), tx.engaged_keys(kind)
        finally:
            connection.rollback()
            connection.close()
