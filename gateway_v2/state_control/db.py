"""The source-of-truth interface, and a transactional in-memory twin for tests.

`KindCounters` is the whole point: a kind's version, cursor position, record count and engaged
count live in ONE row that the writer locks and updates inside the same transaction as the
record. That is what keeps a publish O(1) — the counts a reader needs to prove completeness are
maintained incrementally, never recomputed by scanning the kind.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from gateway_v2.domain.state import SignedRecord, StateKind, Version


class CommitUnknown(Exception):
    """The connection failed DURING COMMIT: the write may or may not be durable.

    Reported as such, never as a failure. The re-hydrator publishes whatever committed.
    """


@dataclass(frozen=True, slots=True)
class KindCounters:
    """One kind's head, as Postgres knows it."""

    version: Version
    feed_seq: int
    count: int
    on_count: int

    def next_version(self, *, epoch_bump: bool) -> Version:
        if epoch_bump:
            return Version(self.version.epoch + 1, 0)
        return Version(self.version.epoch, self.version.seq + 1)


ZERO_COUNTERS = KindCounters(version=Version(1, 0), feed_seq=0, count=0, on_count=0)


@dataclass(frozen=True, slots=True)
class LoggedWrite:
    """One row of the append-only log, enough to replay its content as a new version."""

    log_id: int
    record: SignedRecord
    actor: str


class ControlTx(Protocol):
    """One transaction. Every write locks the kind's counter row first."""

    def counters(self, kind: StateKind, *, lock: bool) -> KindCounters:
        ...

    def set_counters(self, kind: StateKind, counters: KindCounters) -> None:
        ...

    def record(self, kind: StateKind, key: str) -> SignedRecord | None:
        ...

    def engaged(self, kind: StateKind, key: str) -> bool:
        ...

    def upsert(self, record: SignedRecord, *, engaged: bool, actor: str) -> None:
        ...

    def records(self, kind: StateKind) -> tuple[SignedRecord, ...]:
        ...

    def engaged_keys(self, kind: StateKind) -> tuple[str, ...]:
        ...

    def logged(self, log_id: int) -> LoggedWrite | None:
        ...


class ControlDB(Protocol):
    def tx(self) -> contextlib.AbstractContextManager[ControlTx]:
        ...

    def counters(self, kind: StateKind) -> KindCounters:
        ...

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        """Counters, records and engaged keys as ONE consistent read. Repair path only."""
        ...

    def oldest_unpublished_at(self, kind: StateKind, above_feed_seq: int) -> float | None:
        """When the oldest write ABOVE the store's published position was committed.

        `None` when nothing is pending. This is SP2's only possible detector: a write that is
        durable in Postgres and absent from the store is invisible to every gateway by
        construction -- that invisibility IS the defect -- so the control plane has to report it.
        """
        ...


@dataclass
class _State:
    counters: dict[StateKind, KindCounters]
    records: dict[tuple[StateKind, str], SignedRecord]
    engaged: dict[tuple[StateKind, str], bool]
    log: list[LoggedWrite]
    written_at: dict[tuple[StateKind, str], float]

    def copy(self) -> _State:
        return _State(
            counters=dict(self.counters),
            records=dict(self.records),
            engaged=dict(self.engaged),
            log=list(self.log),
            written_at=dict(self.written_at),
        )


class _MemoryTx:
    def __init__(self, state: _State, clock: Callable[[], float] = time.time) -> None:
        self._state = state
        self._clock = clock

    def counters(self, kind: StateKind, *, lock: bool) -> KindCounters:
        del lock  # the twin is serialised by one lock, so FOR UPDATE is implicit
        return self._state.counters.setdefault(kind, ZERO_COUNTERS)

    def set_counters(self, kind: StateKind, counters: KindCounters) -> None:
        self._state.counters[kind] = counters

    def record(self, kind: StateKind, key: str) -> SignedRecord | None:
        return self._state.records.get((kind, key))

    def engaged(self, kind: StateKind, key: str) -> bool:
        return self._state.engaged.get((kind, key), False)

    def upsert(self, record: SignedRecord, *, engaged: bool, actor: str) -> None:
        self._state.records[(record.kind, record.key)] = record
        self._state.engaged[(record.kind, record.key)] = engaged
        # Mirrors `amf_state_record.updated_at = now()`, which is what the real adapter's
        # unpublished-age query reads. Without it the twin could not exercise SP2's detector.
        self._state.written_at[(record.kind, record.key)] = self._clock()
        self._state.log.append(
            LoggedWrite(log_id=len(self._state.log) + 1, record=record, actor=actor),
        )

    def records(self, kind: StateKind) -> tuple[SignedRecord, ...]:
        return tuple(
            record
            for (held, _), record in sorted(
                self._state.records.items(), key=lambda item: item[0][1],
            )
            if held is kind
        )

    def engaged_keys(self, kind: StateKind) -> tuple[str, ...]:
        return tuple(
            sorted(key for (held, key), on in self._state.engaged.items() if held is kind and on),
        )

    def logged(self, log_id: int) -> LoggedWrite | None:
        for entry in self._state.log:
            if entry.log_id == log_id:
                return entry
        return None


class MemoryControlDB:
    """Transactional twin of the Postgres layer. A failed transaction leaves no trace.

    Used by the control-plane tests and by the local end-to-end harness. The concrete psycopg
    implementation satisfies the same Protocols, so the writer code under test is the real one.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._state = _State(
            counters={}, records={}, engaged={}, log=[], written_at={},
        )
        self._lock = threading.Lock()
        self._clock = clock
        self.fail_next = False
        self.commit_unknown_next = False

    @contextlib.contextmanager
    def tx(self) -> Iterator[_MemoryTx]:
        with self._lock:
            work = self._state.copy()
            yield _MemoryTx(work, self._clock)
            if self.fail_next:
                self.fail_next = False
                raise ConnectionError("injected database failure")
            self._state = work
            if self.commit_unknown_next:
                self.commit_unknown_next = False
                raise CommitUnknown("injected: connection lost during COMMIT")

    def counters(self, kind: StateKind) -> KindCounters:
        with self._lock:
            return self._state.counters.get(kind, ZERO_COUNTERS)

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        with self._lock:
            view = _MemoryTx(self._state)
            return (
                self._state.counters.get(kind, ZERO_COUNTERS),
                view.records(kind),
                view.engaged_keys(kind),
            )

    def oldest_unpublished_at(self, kind: StateKind, above_feed_seq: int) -> float | None:
        with self._lock:
            pending = [
                self._state.written_at.get((held, key), 0.0)
                for (held, key), record in self._state.records.items()
                if held is kind and record.feed_seq > above_feed_seq
            ]
        return min(pending) if pending else None

    def log(self) -> Sequence[LoggedWrite]:
        with self._lock:
            return tuple(self._state.log)


def advance(
    counters: KindCounters,
    *,
    epoch_bump: bool,
    is_new: bool,
    engaged_delta: int,
) -> KindCounters:
    """The counter arithmetic one write performs. Pure, so it is testable on its own.

    `count` only ever rises: C36 keeps an explicit OFF record for a revoked key, a disengaged
    switch and an offboarded org, so a record is never removed. A reader relies on that — a
    manifest whose count fell would mean records vanished.
    """
    return replace(
        counters,
        version=counters.next_version(epoch_bump=epoch_bump),
        feed_seq=counters.feed_seq + 1,
        count=counters.count + (1 if is_new else 0),
        on_count=counters.on_count + engaged_delta,
    )
