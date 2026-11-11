"""GW14c — the durable sink against a REAL Postgres. Skipped unless `AMF_PG_DSN` is set.

Everything else in this card's durability half is driven through a `FakeSink`, which proves the
exporter's arithmetic and proves nothing about the SQL. The schema has to actually apply, the
`ON CONFLICT` has to actually suppress a re-insert, and the page-plus-cursor transaction has to
actually be one transaction — none of which a fake can be wrong about.

Follows the `test_lgw05c_pg.py` convention exactly:

    docker run -d --rm --name amf-gw14c-pg -e POSTGRES_PASSWORD=pg \\
      -p 55433:5432 postgres:16-alpine
    AMF_PG_DSN='postgresql://postgres:pg@127.0.0.1:55433/postgres' \\
      .venv/bin/python -m pytest tests/audit_control/test_lgw14c_pg.py -q
    docker rm -f amf-gw14c-pg

The sink reaches Postgres through `state_control.pg.PostgresControlDB.cursor_tx()`, so these
tests also exercise that seam — R2-04's four session bounds, applied as `SET LOCAL` and read back
by `verify_bounds()`, covering the audit tables rather than being re-declared for them.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator

import pytest

from audit_control.cursor import ZERO, OrgCursor, StreamId, parse_id
from audit_control.sink_pg import DurableRecord, PostgresAuditSink
from state_control.pg import PostgresControlDB

DSN = os.environ.get("AMF_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set AMF_PG_DSN to a disposable Postgres to run the durable-sink proofs",
)


@pytest.fixture(scope="module")
def database() -> PostgresControlDB:
    return PostgresControlDB(DSN)


@pytest.fixture(scope="module")
def sink(database: PostgresControlDB) -> PostgresAuditSink:
    """One schema application for the module. It must be idempotent, and is asserted so."""
    built = PostgresAuditSink(database)
    built.init_schema()
    built.init_schema()  # IF NOT EXISTS, twice: a second deploy must not fail
    return built


@pytest.fixture
def org() -> Iterator[str]:
    """A unique tenant per test, so the module's tests do not see each other's rows."""
    yield f"org-{uuid.uuid4().hex[:10]}"


def _record(org: str, ms: int, seq: int, **body: object) -> DurableRecord:
    payload = json.dumps(
        {
            "v": 1,
            "request_id": f"req-{ms}-{seq}",
            "org_id": org,
            "phase": "input",
            "outcome": "allow",
            "at_ns": ms * 1_000_000,
            "detail": {},
            **body,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return DurableRecord(org_id=org, stream_id=StreamId(ms, seq), payload=payload)


# --- the schema, and the bounds it inherits -------------------------------------------------------


def test_the_session_bounds_apply_to_the_audit_sinks_connections(
    database: PostgresControlDB,
) -> None:
    """R2-04's finding was that the four bounds reached no backend in the endorsed topology.
    The audit sink shares that machinery rather than re-declaring it, so `verify_bounds()`
    covering it is the point of the `cursor_tx()` seam."""
    applied = database.verify_bounds()

    assert int(applied["lock_timeout"]) > 0
    assert int(applied["statement_timeout"]) > 0
    assert int(applied["idle_in_transaction_session_timeout"]) > 0


def test_the_schema_applies_and_is_idempotent(sink: PostgresAuditSink, org: str) -> None:
    """The fixture already applied it twice. This proves the tables are actually usable."""
    assert sink.durable_count(org) == 0
    assert sink.cursor_for(org) == OrgCursor(org_id=org)


# --- the page and the cursor are ONE transaction --------------------------------------------------


def test_a_page_and_its_cursor_commit_together(sink: PostgresAuditSink, org: str) -> None:
    records = [_record(org, 1_000, index) for index in range(5)]
    state = OrgCursor(
        org_id=org,
        durable=StreamId(1_000, 4),
        durable_records=5,
        acknowledged=StreamId(1_000, 4),
        acknowledged_records=5,
    )

    inserted = sink.commit_page(records, state)

    assert inserted == 5
    assert sink.durable_count(org) == 5
    read_back = sink.cursor_for(org)
    assert read_back.durable == StreamId(1_000, 4)
    assert read_back.durable_records == 5


def test_re_reading_a_page_inserts_nothing(sink: PostgresAuditSink, org: str) -> None:
    """`ON CONFLICT (org_id, stream_id) DO NOTHING`, against the real primary key.

    This is what makes a crash between the insert and the cursor harmless, and it is the one
    thing a fake sink cannot be trusted about — the idempotence IS the schema.
    """
    records = [_record(org, 2_000, index) for index in range(3)]
    state = OrgCursor(org_id=org, durable=StreamId(2_000, 2), durable_records=3)

    first = sink.commit_page(records, state)
    second = sink.commit_page(records, state)

    assert first == 3
    assert second == 0, "the second attempt must insert no rows"
    assert sink.durable_count(org) == 3


def test_an_empty_page_still_persists_the_cursor(sink: PostgresAuditSink, org: str) -> None:
    """The exporter commits a record-less page to persist a loss count that was computed after
    the last real page. Without this the number would be recomputed every round and never
    become durable."""
    state = OrgCursor(
        org_id=org,
        durable=StreamId(3_000, 9),
        durable_records=10,
        acknowledged=StreamId(3_000, 9),
        acknowledged_records=46,
        records_lost=36,
    )

    assert sink.commit_page((), state) == 0

    read_back = sink.cursor_for(org)
    assert read_back.records_lost == 36
    assert read_back.acknowledged_records == 46
    assert read_back.durable_records == 10


def test_the_cursor_round_trips_every_field(sink: PostgresAuditSink, org: str) -> None:
    state = OrgCursor(
        org_id=org,
        durable=StreamId(4_000, 7),
        durable_records=120,
        acknowledged=StreamId(4_100, 2),
        acknowledged_records=200,
        records_lost=5,
    )
    sink.commit_page((), state)

    assert sink.cursor_for(org) == state


def test_a_fresh_tenant_reads_as_a_zero_cursor(sink: PostgresAuditSink, org: str) -> None:
    """`0-0` is below every real id, so the first page is the whole stream."""
    fresh = sink.cursor_for(org)

    assert fresh.durable == ZERO
    assert fresh.durable.is_zero
    assert fresh.records_lost == 0


# --- the stored row ------------------------------------------------------------------------------


def test_the_body_is_stored_as_the_exact_bytes_the_store_held(
    sink: PostgresAuditSink, database: PostgresControlDB, org: str,
) -> None:
    """`text`, not `jsonb`. jsonb normalises key order and whitespace, which would change the
    bytes the byte model sized and the exporter counted."""
    record = _record(org, 5_000, 1)
    sink.commit_page([record], OrgCursor(org_id=org, durable=StreamId(5_000, 1)))

    with database.cursor_tx() as cursor:
        cursor.execute(
            "SELECT body FROM amf_audit_record WHERE org_id = %s AND stream_id = %s",
            (org, "5000-1"),
        )
        stored = cursor.fetchone()[0]

    assert stored.encode() == record.payload


def test_the_parsed_id_is_what_the_index_orders_on(
    sink: PostgresAuditSink, database: PostgresControlDB, org: str,
) -> None:
    """`'10-1' < '9-1'` as text, so ordering on the id STRING would be wrong in exactly the way
    that makes loss detection skip records."""
    sink.commit_page(
        [_record(org, 9, 1), _record(org, 10, 1)],
        OrgCursor(org_id=org, durable=StreamId(10, 1), durable_records=2),
    )

    with database.cursor_tx() as cursor:
        cursor.execute(
            "SELECT stream_id FROM amf_audit_record WHERE org_id = %s "
            "ORDER BY id_ms, id_seq",
            (org,),
        )
        ordered = [row[0] for row in cursor.fetchall()]

    assert ordered == ["9-1", "10-1"]
    assert sorted(ordered) == ["10-1", "9-1"], "text ordering really is the wrong answer"


def test_the_join_columns_are_extracted_from_the_payload(
    sink: PostgresAuditSink, database: PostgresControlDB, org: str,
) -> None:
    """The customer's join handle is the request id from their SDK error."""
    sink.commit_page(
        [_record(org, 6_000, 1)], OrgCursor(org_id=org, durable=StreamId(6_000, 1)),
    )

    with database.cursor_tx() as cursor:
        cursor.execute(
            "SELECT request_id, phase, outcome, schema_version FROM amf_audit_record "
            "WHERE org_id = %s",
            (org,),
        )
        row = cursor.fetchone()

    assert row == ("req-6000-1", "input", "allow", 1)


def test_a_malformed_payload_is_still_stored(
    sink: PostgresAuditSink, database: PostgresControlDB, org: str,
) -> None:
    """An audit record that cannot be parsed is itself a finding. Dropping it here would mean the
    one record proving something went wrong is the one record that does not survive."""
    broken = DurableRecord(
        org_id=org, stream_id=StreamId(7_000, 1), payload=b"{not json at all",
    )

    assert sink.commit_page([broken], OrgCursor(org_id=org, durable=StreamId(7_000, 1))) == 1

    with database.cursor_tx() as cursor:
        cursor.execute(
            "SELECT body, request_id FROM amf_audit_record WHERE org_id = %s", (org,),
        )
        body, request_id = cursor.fetchone()

    assert body == "{not json at all"
    assert request_id == "", "unparseable means the join columns are empty, not that it is lost"


# --- the exporter over a real sink ----------------------------------------------------------------


def test_the_exporter_drains_a_stream_into_the_real_sink(
    sink: PostgresAuditSink, org: str,
) -> None:
    """End to end with the real SQL underneath, including the loss subtraction."""
    import asyncio

    from audit_control.export import AuditExporter

    class Stream:
        def __init__(self) -> None:
            self.entries = [
                (f"8000-{index}", _record(org, 8_000, index).payload) for index in range(10)
            ]

        async def read(
            self, _org: str, *, after: str = "0-0", limit: int = 256,
        ) -> tuple[tuple[str, bytes], ...]:
            floor = parse_id(after)
            above = [(i, p) for i, p in self.entries if parse_id(i) > floor]
            return tuple(above[:limit])

        async def first_id(self, _org: str) -> str | None:
            return self.entries[0][0] if self.entries else None

        async def length(self, _org: str) -> int:
            return len(self.entries)

    stream = Stream()
    exporter = AuditExporter(stream, sink)
    exporter.note_acknowledged(org, 10)

    export = asyncio.run(exporter.export_org(org))

    assert export.exported == 10
    assert export.lost == 0
    assert sink.durable_count(org) == 10
    assert sink.cursor_for(org).durable == StreamId(8_000, 9)

    # Now lose the tail: the writer appended 15, only 10 ever reached the sink.
    exporter.note_acknowledged(org, 15)
    stream.entries = []
    after_loss = asyncio.run(exporter.export_org(org))

    assert after_loss.lost == 5
    assert sink.cursor_for(org).records_lost == 5, "the loss has to be DURABLE, not in RAM"


def test_cursors_lists_every_tenant(sink: PostgresAuditSink) -> None:
    """The exporter's metrics rollup read. O(tenants), off every hot path."""
    first, second = f"org-{uuid.uuid4().hex[:8]}", f"org-{uuid.uuid4().hex[:8]}"
    sink.commit_page((), OrgCursor(org_id=first, durable_records=3, acknowledged_records=3))
    sink.commit_page((), OrgCursor(org_id=second, durable_records=4, acknowledged_records=9))

    found = {cursor.org_id: cursor for cursor in sink.cursors()}

    assert found[first].durable_records == 3
    assert found[second].acknowledged_records == 9
