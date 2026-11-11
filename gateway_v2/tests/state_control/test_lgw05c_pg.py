"""GW05c phase 4c — the Postgres adapter, against a real server.

Skipped unless `AMF_PG_DSN` points at a disposable Postgres, so CI and the default local run do
not need a database. The adapter is driven through the REAL `StateWriter` and the REAL
`Rehydrator`, so what is verified is the writer's behaviour over Postgres rather than SQL strings.

Run it with a throwaway server:

    docker run -d --rm --name amf-gw05c-pg -e POSTGRES_PASSWORD=pg \\
      -p 55432:5432 postgres:16-alpine
    AMF_PG_DSN='postgresql://postgres:pg@127.0.0.1:55432/postgres' \\
      .venv/bin/python -m pytest tests/state_control/test_lgw05c_pg.py -q
    docker rm -f amf-gw05c-pg
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import StateKind, Version
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.pg import PostgresControlDB
from state_control.publisher import MemoryStore
from state_control.rehydrate import MISSING, STALE, Rehydrator
from state_control.writer import OK, StateWriter
from tests.plan.test_lgw05c_delta import _draft

DSN = os.environ.get("AMF_PG_DSN", "")

pytestmark = pytest.mark.skipif(not DSN, reason="AMF_PG_DSN is not set")

SECRET = b"gw05c-pg-test-secret"


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


@pytest.fixture
def db() -> Iterator[PostgresControlDB]:
    """A schema of its own per test, dropped afterwards, so nothing is shared."""
    import psycopg

    schema = f"amf_gw05c_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
    scoped = f"{DSN}?options=-csearch_path%3D{schema}"
    database = PostgresControlDB(scoped)
    database.init_schema()
    try:
        yield database
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _writer(db: PostgresControlDB) -> tuple[StateWriter, MemoryStore]:
    store = MemoryStore()
    return StateWriter(db, store, SECRET), store


def test_a_write_commits_and_publishes(db: PostgresControlDB) -> None:
    writer, store = _writer(db)

    outcome = writer.plan_set("org-a", _plan_body("org-a"))

    assert outcome.status == OK
    assert outcome.version == Version(1, 1)
    assert outcome.feed_seq == 1
    assert store.record_writes == 1
    assert db.counters(StateKind.PLAN).count == 1


def test_counters_are_maintained_incrementally(db: PostgresControlDB) -> None:
    """The columns that let a reader prove completeness without a scan."""
    writer, _store = _writer(db)

    writer.plan_set("org-a", _plan_body("org-a"))
    writer.plan_set("org-a", _plan_body("org-a"))
    writer.plan_set("org-b", _plan_body("org-b"))
    writer.plan_offboard("org-b")

    counters = db.counters(StateKind.PLAN)
    assert counters.count == 2, "an update is not a new record, an offboard is not a removal"
    assert counters.feed_seq == 4
    assert counters.version == Version(1, 4)


def test_on_count_tracks_engagement(db: PostgresControlDB) -> None:
    writer, _store = _writer(db)

    writer.killswitch("org:a", on=True)
    writer.killswitch("org:b", on=True)
    writer.killswitch("org:a", on=False)

    counters = db.counters(StateKind.KS)
    assert (counters.count, counters.on_count) == (2, 1)
    _c, _records, engaged = db.snapshot(StateKind.KS)
    assert engaged == ("org:b",)


def test_a_record_round_trips_byte_exactly(db: PostgresControlDB) -> None:
    """body is text, not jsonb: a jsonb round trip would invalidate every signature."""
    writer, _store = _writer(db)
    body: dict[str, object] = {"z": 1, "a": {"nested": [1, 2, 3]}, "text": "caf\u00e9"}
    writer.put(StateKind.BUDGET, "org-a", body)

    with db.tx() as tx:
        stored = tx.record(StateKind.BUDGET, "org-a")

    assert stored is not None
    from gateway_v2.runtime.state_sig import canonical_body, decode_record, encode_record

    assert stored.body == canonical_body(body)
    assert decode_record(SECRET, StateKind.BUDGET, encode_record(stored)) == stored


def test_a_failed_transaction_leaves_no_trace(db: PostgresControlDB) -> None:
    writer, _store = _writer(db)
    writer.plan_set("org-a", _plan_body("org-a"))

    class Boom(Exception):
        pass

    with pytest.raises(Boom), db.tx() as tx:
        tx.set_counters(StateKind.PLAN, db.counters(StateKind.PLAN))
        tx.upsert(
            next(iter(db.snapshot(StateKind.PLAN)[1])),
            engaged=False,
            actor="test",
        )
        raise Boom

    assert db.counters(StateKind.PLAN).count == 1


def test_a_bulk_onboard_is_one_transaction(db: PostgresControlDB) -> None:
    writer, store = _writer(db)

    outcome = writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 201)],
    )

    assert outcome.records == 200
    assert db.counters(StateKind.PLAN).count == 200
    assert len(store.nudges) == 1


def test_a_rollback_is_a_new_epoch(db: PostgresControlDB) -> None:
    writer, _store = _writer(db)
    writer.plan_set("org-a", _plan_body("org-a"))
    writer.plan_set("org-a", _plan_body("org-a"))

    outcome = writer.rollback(1)

    assert outcome.version == Version(2, 0)
    assert db.counters(StateKind.PLAN).feed_seq == 3


def test_a_snapshot_is_consistent_and_lock_free(db: PostgresControlDB) -> None:
    """R2-04: a republish must not wait behind a writer holding the counter row."""
    writer, _store = _writer(db)
    for position in range(1, 21):
        writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    with db.tx() as holding:
        holding.counters(StateKind.PLAN, lock=True)  # FOR UPDATE held open
        counters, records, engaged = db.snapshot(StateKind.PLAN)

    assert counters.count == 20
    assert len(records) == 20
    assert engaged == ()


def test_the_rehydrator_diagnoses_and_repairs_over_postgres(db: PostgresControlDB) -> None:
    writer, store = _writer(db)
    rehydrator = Rehydrator(
        db, store, writer, SECRET, stale_grace_s=0.0, kinds=(StateKind.PLAN,),
    )
    for position in range(1, 11):
        writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    assert rehydrator.diagnose(StateKind.PLAN) is None

    store.flush()
    assert rehydrator.diagnose(StateKind.PLAN) == MISSING
    summary = rehydrator.round_once()

    assert [event.records for event in summary.repairs] == [10]
    assert rehydrator.diagnose(StateKind.PLAN) is None


def test_an_unpublished_write_is_diagnosed_stale_over_postgres(
    db: PostgresControlDB,
) -> None:
    from state_control.publisher import BrokenPublisher

    store = MemoryStore()
    good = StateWriter(db, store, SECRET)
    broken = StateWriter(db, BrokenPublisher(), SECRET)
    rehydrator = Rehydrator(
        db, store, good, SECRET, stale_grace_s=0.0, kinds=(StateKind.PLAN,),
    )
    good.plan_set("org-a", _plan_body("org-a"))
    outcome = broken.plan_set("org-b", _plan_body("org-b"))

    assert outcome.durable is True
    assert rehydrator.diagnose(StateKind.PLAN) == STALE
    rehydrator.round_once()
    assert rehydrator.diagnose(StateKind.PLAN) is None


def test_session_bounds_are_set_on_every_connection(db: PostgresControlDB) -> None:
    """A bound only some connections carry is not a bound (R2-04)."""
    with db.tx() as tx:
        tx._cur.execute(
            "SELECT current_setting('lock_timeout'), current_setting('statement_timeout'), "
            "current_setting('idle_in_transaction_session_timeout')",
        )
        lock, statement, idle = tx._cur.fetchone()

    assert lock == "2s"
    assert statement == "5s"
    assert idle == "5s"
