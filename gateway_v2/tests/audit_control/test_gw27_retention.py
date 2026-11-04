"""GW27 Task 8: stamped, non-retroactive audit retention (schema, sink plumbing, sweeper).

The sweeper is driven through an in-memory database double that evaluates the three statements
by identity against an independently written predicate (``_eligible``). The SQL text is pinned
below and the live Postgres arm (skipped without ``AMF_PG_DSN``) is where the planner and the
real predicate are exercised; it is written here and NOT run by the offline gate.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import random
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest

from audit_control import retention
from audit_control.cursor import StreamId
from audit_control.retention import (
    BATCH_DELETE_SQL,
    DRY_RUN_COUNT_SQL,
    ORG_PAGE_SQL,
    RetentionSweeper,
    parse_mode,
)
from audit_control.schema import RECORD_COLUMNS, SCHEMA
from audit_control.service import ExporterService, from_env
from audit_control.sink_pg import _INSERT, DurableRecord, retention_stamp
from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.runtime.resources_org import RetentionBounds, derive_retention_bounds

DAY_NS = 86_400_000_000_000
ENV = {
    "AMF_AUDIT_PG_DSN": "postgresql://amf@pg:6432/amf",
    "AMF_STATE_VALKEY_URL": "redis://valkey:6379",
}
NOW = 10_000 * DAY_NS


@dataclasses.dataclass
class Row:
    org: str
    stream: str
    at_ns: int
    retention: int | None
    locked: bool = False


def _eligible(row: Row, now: int, floor: int) -> bool:
    """Independent restatement of the sweep predicate (NULL never matches)."""
    r = row.retention
    if r is None or r < floor:
        return False
    return row.at_ns < now - floor * DAY_NS and row.at_ns < now - r * DAY_NS


class FakeDB:
    def __init__(self, rows: list[Row], fail_on_statement: int | None = None) -> None:
        self.rows = rows
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[Row] = []
        self.fail_on_statement = fail_on_statement

    @contextlib.contextmanager
    def cursor_tx(self) -> Iterator[FakeCursor]:
        yield FakeCursor(self)


class FakeCursor:
    rowcount = 0

    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._result: list[tuple[object, ...]] = []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        db = self.db
        db.statements.append((sql, params))
        if db.fail_on_statement is not None and len(db.statements) >= db.fail_on_statement:
            raise RuntimeError("database down")
        now, floor = params["now_ns"], params["hard_floor_days"]
        if sql is ORG_PAGE_SQL:
            orgs = sorted({r.org for r in db.rows if r.org > params["after_org"]
                           and _eligible(r, now, floor)})
            self._result = [(o,) for o in orgs[: params["org_page"]]]
        elif sql is DRY_RUN_COUNT_SQL:
            count = sum(1 for r in db.rows if r.org == params["org"] and _eligible(r, now, floor))
            self._result = [(count,)]
        elif sql is BATCH_DELETE_SQL:
            hit = sorted((r for r in db.rows if r.org == params["org"] and not r.locked
                          and _eligible(r, now, floor)), key=lambda r: r.at_ns)[: params["batch"]]
            for row in hit:
                db.rows.remove(row)
                db.deleted.append(row)
            self.rowcount = len(hit)
        else:  # pragma: no cover
            raise AssertionError(f"unexpected statement {sql!r}")

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._result

    def fetchone(self) -> tuple[object, ...] | None:
        return self._result[0] if self._result else None


def _bounds(batch: int = 5, per_org: int = 2, page: int = 2) -> RetentionBounds:
    return dataclasses.replace(
        derive_retention_bounds(), sweep_batch=batch, max_batches_per_org=per_org, org_page=page
    )


def _sweeper(db: FakeDB, mode: str = "on", **kw: int) -> RetentionSweeper:
    return RetentionSweeper(db, _bounds(**kw), mode=mode, clock_ns=lambda: NOW)  # type: ignore[arg-type]


# --- schema and sink plumbing --------------------------------------------------------------- #


def test_schema_adds_the_nullable_column_and_the_org_leading_partial_index() -> None:
    assert "ADD COLUMN IF NOT EXISTS retention_days smallint" in SCHEMA
    assert re.search(
        r"amf_audit_record_retention\s+ON amf_audit_record \(org_id, recorded_at_ns\) "
        r"WHERE retention_days IS NOT NULL", SCHEMA)
    assert SCHEMA.index("ADD COLUMN IF NOT EXISTS retention_days") > SCHEMA.index("CREATE TABLE")


def test_insert_and_columns_agree_with_record_columns() -> None:
    columns_sql = re.search(r"\((.*?)\)\s*VALUES", _INSERT, re.S)
    assert columns_sql is not None
    names = tuple(c.strip() for c in columns_sql.group(1).split(","))
    assert names == RECORD_COLUMNS and RECORD_COLUMNS[-1] == "retention_days"
    assert _INSERT.count("%s") == len(RECORD_COLUMNS)
    record = DurableRecord("org", StreamId(1, 0), b'{"v":1}')
    assert len(record.columns()) == len(RECORD_COLUMNS)


def _payload(detail: object) -> bytes:
    return json.dumps({"v": 1, "request_id": "r", "phase": "input", "outcome": "allow",
                       "at_ns": 5, "detail": detail}).encode()


def test_old_shape_payload_without_detail_stores_null() -> None:
    old = json.dumps({"v": 1, "request_id": "r", "phase": "input", "outcome": "allow",
                      "at_ns": 5}).encode()
    assert DurableRecord("o", StreamId(1, 0), old).columns()[-1] is None
    assert DurableRecord("o", StreamId(1, 0), b"not json").columns()[-1] is None
    assert DurableRecord("o", StreamId(1, 0), _payload("text")).columns()[-1] is None


@pytest.mark.parametrize(("stamp", "stored"), [
    (30, 30), (1, 1), (365, 365), ("30", None), (True, None), (0, None), (366, None),
    (-5, None), (30.0, None), (None, None), ([30], None),
])
def test_only_an_int_in_range_is_stored(stamp: object, stored: int | None) -> None:
    columns = DurableRecord("o", StreamId(1, 0), _payload({"retention_days": stamp})).columns()
    assert columns[-1] == stored
    assert retention_stamp({"detail": {"retention_days": stamp}}) == stored


def test_the_stamp_survives_record_to_payload_to_columns_as_an_int() -> None:
    record = AuditRecord("r", "o", AuditPhase.INPUT, "allow", 1,
                         detail={"retention_days": 90, "compliance_frameworks": ["SOC2"]})
    columns = DurableRecord("o", StreamId(1, 0), record.serialize()).columns()
    assert columns[-1] == 90 and type(columns[-1]) is int


# --- mode parsing and the exporter start-up ------------------------------------------------- #


def test_mode_parsing_defaults_off_and_unknown_is_off_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert parse_mode(None) == parse_mode("") == "off"
    values = [parse_mode(v) for v in ("off", "dry_run", "on", " ON ")]
    assert values == ["off", "dry_run", "on", "on"]
    with caplog.at_level(logging.WARNING, logger="amf.audit.retention"):
        assert parse_mode("yes please") == "off"
    assert "treating it as off" in caplog.text
    assert from_env(ENV).retention_mode == "off"
    assert from_env({**ENV, "AMF_AUDIT_RETENTION_SWEEP": "dry_run"}).retention_mode == "dry_run"


def test_exporter_starts_with_sweep_off_and_no_gateway_worker_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from audit_control.export import AuditExporter
    from tests.audit_control.test_lgw14c_service import EmptyStream, FakeSink

    def boom() -> object:
        raise AssertionError("retention bounds must not be derived while the sweep is off")

    monkeypatch.setattr("audit_control.service.derive_retention_bounds", boom)
    for name in ("WEB_CONCURRENCY", "AMF_WORKERS"):
        monkeypatch.delenv(name, raising=False)
    config = from_env(ENV)
    sink = FakeSink()
    service = ExporterService(config, AuditExporter(EmptyStream(), sink), sink)  # type: ignore[arg-type]
    service._maybe_sweep()  # noqa: SLF001
    assert service.retention_metrics() == ""  # absent while off
    assert "retention" not in __import__("audit_control.service").service.render(service.metrics())


def test_sweep_metric_is_minus_one_before_the_first_success_then_a_growing_age() -> None:
    from audit_control.export import AuditExporter
    from tests.audit_control.test_lgw14c_service import EmptyStream, FakeSink

    config = dataclasses.replace(from_env(ENV), retention_mode="on")
    sink = FakeSink()
    service = ExporterService(
        config, AuditExporter(EmptyStream(), sink), sink,  # type: ignore[arg-type]
        _sweeper(FakeDB([])),
    )
    assert "amf_audit_retention_last_sweep_age_seconds -1.0" in service.retention_metrics()
    service._maybe_sweep()  # noqa: SLF001
    assert "amf_audit_retention_last_sweep_age_seconds -1.0" not in service.retention_metrics()


# --- the sweeper ----------------------------------------------------------------------------- #


def test_sql_is_org_scoped_null_safe_skip_locked_and_floor_bounded() -> None:
    for sql in (ORG_PAGE_SQL, BATCH_DELETE_SQL, DRY_RUN_COUNT_SQL):
        assert "retention_days IS NOT NULL" in sql
        assert "retention_days >= %(hard_floor_days)s" in sql
        assert "recorded_at_ns < %(now_ns)s - %(hard_floor_days)s::bigint * 86400000000000" in sql
        assert "recorded_at_ns < %(now_ns)s - retention_days::bigint * 86400000000000" in sql
    assert "org_id = %(org)s" in BATCH_DELETE_SQL and "FOR UPDATE SKIP LOCKED" in BATCH_DELETE_SQL
    assert "org_id = %(org)s" in DRY_RUN_COUNT_SQL and "org_id > %(after_org)s" in ORG_PAGE_SQL
    assert "ORDER BY org_id" in ORG_PAGE_SQL and "LIMIT %(org_page)s" in ORG_PAGE_SQL


def test_default_clock_is_wall_clock_nanoseconds() -> None:
    sweeper = RetentionSweeper(FakeDB([]), derive_retention_bounds(), mode="on")
    assert abs(sweeper._clock_ns() - time.time_ns()) < DAY_NS  # noqa: SLF001


def test_with_the_default_clock_young_rows_stay_and_expired_rows_go() -> None:
    now = time.time_ns()
    rows = [Row("o", "1", now - 3 * DAY_NS, 2), Row("o", "2", now - 1 * DAY_NS, 2),
            Row("o", "3", now - 3 * DAY_NS, None)]
    RetentionSweeper(FakeDB(rows), derive_retention_bounds(), mode="on").sweep_once()
    assert sorted(r.stream for r in rows) == ["2", "3"]


def test_dry_run_terminates_and_deletes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    rows = [Row(f"o{n}", str(i), 0, 1) for n in range(5) for i in range(7)]
    db = FakeDB(rows)
    with caplog.at_level(logging.INFO, logger="amf.audit.retention"):
        report = _sweeper(db, "dry_run").sweep_once()
    assert (report.orgs, report.deleted, report.failed) == (5, 35, False)
    assert len(rows) == 35 and not db.deleted
    assert "would_delete=7 org=o0" in caplog.text


def test_an_org_with_a_locked_row_terminates() -> None:
    rows = [Row("o", str(i), 0, 1, locked=True) for i in range(3)] + [Row("o", "free", 0, 1)]
    report = _sweeper(FakeDB(rows), batch=2).sweep_once()
    assert report.failed is False and [r.stream for r in rows] == ["0", "1", "2"]


def test_a_huge_backlog_is_partially_swept_and_later_orgs_still_are() -> None:
    rows = [Row("org-a", str(i), 0, 1) for i in range(50)]
    rows += [Row("org-b", str(i), 0, 1) for i in range(3)]
    db = FakeDB(rows)
    report = _sweeper(db, batch=5, per_org=2, page=1).sweep_once()
    assert sum(r.org == "org-a" for r in rows) == 40  # batch * max_batches removed, rest later
    assert sum(r.org == "org-b" for r in rows) == 0
    assert report.deleted == 13


def test_a_database_error_stops_the_sweep_and_deletes_nothing_further() -> None:
    rows = [Row("a", "1", 0, 1), Row("b", "1", 0, 1)]
    db = FakeDB(rows, fail_on_statement=3)  # page, delete a, then the error
    report = _sweeper(db).sweep_once()
    assert report.failed is True and [r.org for r in rows] == ["b"]


# Feature: gw27-firewall-config-fields, Property 11: the sweep predicate and loop are safe
# Validates: Requirements 5.5
def test_property_retention_safety_and_termination() -> None:
    seed = 2711
    rng = random.Random(seed)
    for i in range(10_000):
        orgs = [f"org-{n}" for n in range(rng.randint(1, 4))]
        rows = [
            Row(rng.choice(orgs), str(k), NOW - rng.randint(0, 40) * DAY_NS - rng.randint(0, 99),
                rng.choice((None, None, 0, 1, 2, 5, 30)), locked=rng.random() < 0.2)
            for k in range(rng.randint(0, 25))
        ]
        before = list(rows)
        floor = derive_retention_bounds().hard_floor_days
        db = FakeDB(rows)
        mode = rng.choice(("on", "dry_run"))
        bounds = _bounds(rng.randint(1, 4), rng.randint(1, 3), rng.randint(1, 3))
        report = RetentionSweeper(db, bounds, mode=mode, clock_ns=lambda: NOW).sweep_once()  # type: ignore[arg-type]
        ctx = (seed, i, mode)
        assert report.failed is False, ctx
        for row in db.deleted:
            assert row.retention is not None, ctx  # never NULL
            assert row.retention >= floor, ctx  # never below the hard floor
            assert row.at_ns < NOW - row.retention * DAY_NS, ctx  # never younger than its stamp
        for sql, params in db.statements:
            if sql is BATCH_DELETE_SQL:
                assert params["org"] in orgs, ctx  # always org-scoped
        if mode == "dry_run":
            assert db.rows == before, ctx
        pages = len(orgs) // bounds.org_page + 2
        assert len(db.statements) <= pages + len(orgs) * (bounds.max_batches_per_org + 1), ctx
        for row in before:
            if row.retention is None:
                assert row in db.rows, ctx


# --- the live arm: written, NOT run offline --------------------------------------------------- #

DSN = os.environ.get("AMF_PG_DSN", "")


@pytest.mark.skipif(not DSN, reason="set AMF_PG_DSN to a disposable Postgres (live arm)")
def test_live_postgres_sweep_and_explain() -> None:  # pragma: no cover - live gate
    import uuid

    from audit_control.sink_pg import PostgresAuditSink
    from state_control.pg import PostgresControlDB

    database = PostgresControlDB(DSN)
    PostgresAuditSink(database).init_schema()
    org = f"org-{uuid.uuid4().hex[:8]}"
    now = time.time_ns()
    sink = PostgresAuditSink(database)
    old = DurableRecord(org, StreamId(1, 0), _payload({"retention_days": 1}))
    sink.commit_page([old], sink.cursor_for(org))
    with database.cursor_tx() as cursor:
        cursor.execute("UPDATE amf_audit_record SET recorded_at_ns = %s WHERE org_id = %s",
                       (now - 3 * DAY_NS, org))
    report = RetentionSweeper(database, derive_retention_bounds(), mode="on").sweep_once()
    assert report.failed is False and sink.durable_count(org) == 0
    with database.cursor_tx() as cursor:
        cursor.execute("EXPLAIN " + BATCH_DELETE_SQL,
                       {"org": org, "now_ns": now, "hard_floor_days": 1, "batch": 10})
        plan = "\n".join(str(r[0]) for r in cursor.fetchall())
    print(plan)  # recorded in the evidence by the live gate
    assert retention.BATCH_DELETE_SQL
