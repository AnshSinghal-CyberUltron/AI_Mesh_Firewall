"""The Postgres source of truth. One short-lived connection per operation.

Every session sets four timeouts. They belong to GW05b (R2-04: one idle writer transaction
holding `FOR UPDATE` stalled re-hydration for 38 s, and two re-hydrators blocked on the same
lock), and they are set here because that is where a connection is opened — a bound that only
some connections carry is not a bound.

`snapshot` reads `REPEATABLE READ, READ ONLY` and takes **no** row lock. RC2 read the version row
`FOR SHARE`, which waits behind a writer holding it `FOR UPDATE`; that is exactly how a single
idle transaction stalled re-hydration indefinitely. A repeatable-read snapshot sees one
consistent generation without waiting for anybody, which is all a republish needs.

Counters live in one row per kind and are updated inside the write transaction. That is what
keeps a publish O(1): `count` and `on_count` are maintained incrementally rather than recomputed
by scanning the kind, which is the R2-02 defect.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, Version
from state_control.db import ZERO_COUNTERS, CommitUnknown, KindCounters, LoggedWrite
from state_control.schema import SCHEMA

LOG = logging.getLogger("amf.state.pg")

_COLUMNS = (
    "kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body"
)
_PLACEHOLDERS = ", ".join(["%s"] * 10)


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
    ) -> None:
        import psycopg

        self._psycopg = psycopg
        self._dsn = dsn
        self._connect_timeout_s = connect_timeout_s
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

    def _connect(self, *, read_only: bool = False) -> Any:
        connection = self._psycopg.connect(
            self._dsn,
            autocommit=False,
            connect_timeout=self._connect_timeout_s,
            options=self._options,
        )
        if read_only:
            # Lock-free: a repeatable-read snapshot never waits behind a held FOR UPDATE (R2-04).
            connection.read_only = True
            connection.isolation_level = self._psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def init_schema(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(SCHEMA)

    @contextlib.contextmanager
    def tx(self) -> Iterator[PostgresTx]:
        """An exception in the body rolls back. A failure OF the commit is CommitUnknown."""
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
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
        with self.tx() as tx:
            return tx.counters(kind, lock=False)

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        """One consistent generation, read without taking a single lock."""
        connection = self._connect(read_only=True)
        try:
            with connection.cursor() as cursor:
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
