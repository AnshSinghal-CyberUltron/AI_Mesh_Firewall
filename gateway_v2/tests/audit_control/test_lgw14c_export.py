"""GW14c phase 6: the durable sink, the two high-water marks, and L14c-3.

The finding being closed is R2-11's M3, stated exactly: **a flush erased 36% of the audit records
while `audit_completeness_ratio` read 1.0.** So the assertions are about what the number does when
records are gone:

* a flush that overtakes the durable cursor counts the loss, EXACTLY, and the ratio drops;
* a trim BELOW the durable cursor is retention, not loss, and the ratio does not move -- because
  a metric that screams during normal operation teaches everyone to ignore it;
* the page and the cursor commit together, so a crash between them re-reads and does not
  double-count;
* stream ids are compared as parsed pairs, because `'10-1' < '9-1'` as text would make loss
  detection skip a decade of ids silently;
* the cursor does not stall on records a trim removed, which would be the GW05c `store_ahead`
  defect in a new mechanism.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence

import pytest

from audit_control.cursor import OrgCursor, StreamId, parse_id, total_loss
from audit_control.export import AuditExporter, RoundSummary
from audit_control.sink_pg import DurableRecord


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakeStream:
    """An in-memory audit stream with a trim, so loss can be staged precisely."""

    def __init__(self) -> None:
        self.entries: list[tuple[StreamId, bytes]] = []
        self._seq = 0

    def add(self, count: int, org: str = "org-a") -> list[StreamId]:
        added = []
        for _ in range(count):
            self._seq += 1
            ident = StreamId(1_000, self._seq)
            payload = (
                b'{"v":1,"request_id":"req-%d","org_id":"%s","phase":"input",'
                b'"outcome":"allow","at_ns":1,"detail":{}}'
                % (self._seq, org.encode())
            )
            self.entries.append((ident, payload))
            added.append(ident)
        return added

    def trim_to(self, keep: int) -> int:
        """Drop the oldest entries, keeping the newest `keep`. Returns how many it removed."""
        removed = max(len(self.entries) - keep, 0)
        self.entries = self.entries[removed:]
        return removed

    def flush(self) -> int:
        removed = len(self.entries)
        self.entries = []
        return removed

    async def read(
        self, org: str, *, after: str = "0-0", limit: int = 256,
    ) -> tuple[tuple[str, bytes], ...]:
        del org
        floor = parse_id(after)
        above = [(i, p) for i, p in self.entries if i > floor]
        return tuple((str(i), p) for i, p in above[:limit])

    async def first_id(self, org: str) -> str | None:
        del org
        return str(self.entries[0][0]) if self.entries else None

    async def length(self, org: str) -> int:
        del org
        return len(self.entries)


class FakeSink:
    """An in-memory durable sink with the same idempotence as the real primary key."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], DurableRecord] = {}
        self.cursors: dict[str, OrgCursor] = {}
        self.commits = 0
        self.fail_commit = False

    def cursor_for(self, org_id: str) -> OrgCursor:
        return self.cursors.get(org_id, OrgCursor(org_id=org_id))

    def commit_page(
        self, records: Sequence[DurableRecord], cursor_state: OrgCursor,
    ) -> int:
        if self.fail_commit:
            raise RuntimeError("commit refused")
        self.commits += 1
        inserted = 0
        for record in records:
            key = (record.org_id, str(record.stream_id))
            if key not in self.rows:
                self.rows[key] = record
                inserted += 1
        self.cursors[cursor_state.org_id] = cursor_state
        return inserted


def _exporter(stream: FakeStream, sink: FakeSink, **kwargs: int) -> AuditExporter:
    return AuditExporter(stream, sink, **kwargs)


# --- stream ids are compared as parsed pairs ------------------------------------------------------


def test_stream_ids_order_numerically_not_lexically() -> None:
    """`'10-1' < '9-1'` as text. A string comparison would make loss detection skip a decade of
    ids, silently, and `records_lost` would read zero in exactly the case this card exists for."""
    assert parse_id("9-1") < parse_id("10-1")
    assert parse_id("1000-2") > parse_id("1000-1")
    assert sorted([parse_id("10-0"), parse_id("9-9")]) == [parse_id("9-9"), parse_id("10-0")]


def test_a_bare_millis_id_parses() -> None:
    assert parse_id("1700000000000") == StreamId(1_700_000_000_000, 0)


def test_a_malformed_id_is_refused_rather_than_silently_zero() -> None:
    """A malformed id reading as `0-0` would sit below everything, so the cursor would never
    advance past it and the export would loop on one page forever -- the GW05c `store_ahead`
    defect in a different mechanism."""
    for bad in ("", "   ", "abc", "1-x", "x-1"):
        with pytest.raises(ValueError):
            parse_id(bad)


# --- the loss rule: counts, not positions ---------------------------------------------------------


def test_a_trim_below_the_durable_cursor_is_retention_not_loss() -> None:
    """The mechanism working. A metric that screams during normal operation gets ignored."""
    assert total_loss(acknowledged=100, durable=100, pending=0) == 0


def test_a_trim_that_overtakes_the_durable_cursor_is_loss() -> None:
    assert total_loss(acknowledged=500, durable=100, pending=0) == 400


def test_records_still_waiting_to_be_exported_are_not_loss() -> None:
    """Otherwise the metric would read non-zero through every healthy minute of operation."""
    assert total_loss(acknowledged=100, durable=60, pending=40) == 0


def test_a_flush_of_a_partly_exported_stream_loses_only_the_unexported_part() -> None:
    """The case that broke the first version of this rule. Deriving loss from the writer's XTRIM
    count would report 100 lost here, because a flush removes the already-durable records too.
    The subtraction reports 36, which is the truth."""
    assert total_loss(acknowledged=100, durable=64, pending=0) == 36


def test_the_loss_total_is_idempotent() -> None:
    """A lifetime total, not a delta: a round that double-counted its own loss is impossible."""
    assert total_loss(100, 64, 0) == total_loss(100, 64, 0) == 36


def test_loss_never_reads_negative() -> None:
    """The three inputs are observed at slightly different moments. A counter that can go
    backwards publishes negative rates (R2-11, M4)."""
    assert total_loss(acknowledged=10, durable=20, pending=5) == 0


# --- L14c-3: the flush ----------------------------------------------------------------------------


def test_a_flush_counts_loss_exactly_and_completeness_never_reads_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """**L14c-3.** This is R2-11's M3 reproduced and then fixed: under the old behaviour the ratio
    read 1.0 through exactly this sequence."""
    stream, sink = FakeStream(), FakeSink()
    # One page of 64, so the export stops exactly where round 2's measurement did.
    exporter = _exporter(stream, sink, page=64, max_pages=1)

    # 100 records produced, 64 exported...
    stream.add(100)
    first = _run(exporter.export_org("org-a"))
    assert first.cursor.durable_records == 64

    # ...then the store is flushed. 36 records were acknowledged and are now gone: the exact
    # proportion round 2 measured.
    # The writer appended 100 records for this tenant, and it is the only component that can
    # still say so once they are gone.
    exporter.note_acknowledged("org-a", 100)
    stream.flush()

    with caplog.at_level(logging.WARNING, logger="amf.audit.export"):
        after = _run(exporter.export_org("org-a"))

    assert after.lost == 36, "the loss must be counted exactly, not estimated"
    assert after.cursor.records_lost == 36
    summary = RoundSummary((after,))
    assert summary.completeness_ratio == pytest.approx(64 / 100)
    assert summary.completeness_ratio < 1.0, "the M3 defect was this reading 1.0"
    assert any("audit_records_lost" in r.getMessage() for r in caplog.records)


def test_a_full_drain_reports_complete_because_nothing_was_lost() -> None:
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    stream.add(50)

    export = _run(exporter.export_org("org-a"))

    assert export.lost == 0
    assert export.cursor.durable_records == 50
    assert RoundSummary((export,)).completeness_ratio == 1.0


def test_trimming_after_export_does_not_move_the_ratio() -> None:
    """Retention is the mechanism working. Only loss counts as loss."""
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    stream.add(100)
    _run(exporter.export_org("org-a"))

    exporter.note_acknowledged("org-a", 100)
    stream.trim_to(20)
    export = _run(exporter.export_org("org-a"))

    assert export.lost == 0
    assert export.cursor.records_lost == 0
    assert RoundSummary((export,)).completeness_ratio == 1.0


# --- the cursor must not stall --------------------------------------------------------------------


def test_the_cursor_moves_past_records_a_trim_removed() -> None:
    """If the cursor kept asking for records that no longer exist it would never advance again,
    and the loss would become permanent silence instead of one counted event."""
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    stream.add(10)
    exporter.note_acknowledged("org-a", 10)
    stream.trim_to(3)

    export = _run(exporter.export_org("org-a"))

    assert export.lost == 7
    assert export.exported == 3, "the surviving records are still exported"
    assert len(sink.rows) == 3


def test_the_first_record_after_a_trim_is_not_skipped() -> None:
    """The page read is EXCLUSIVE of the cursor, so after a trim the cursor must sit just BELOW
    the oldest survivor rather than on it -- otherwise one record per trim vanishes with no
    counter moving, turning a visible loss into a silent one."""
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    ids = stream.add(5)
    exporter.note_acknowledged("org-a", 5)
    stream.trim_to(2)

    _run(exporter.export_org("org-a"))

    stored = {key[1] for key in sink.rows}
    assert str(ids[3]) in stored
    assert str(ids[4]) in stored


# --- idempotence ----------------------------------------------------------------------------------


def test_re_reading_a_page_after_a_crash_inserts_nothing() -> None:
    """The cursor and the page commit together; if only the page landed, the re-read is safe."""
    stream, sink = FakeStream(), FakeSink()
    stream.add(10)
    exporter = _exporter(stream, sink)
    _run(exporter.export_org("org-a"))
    rows_after_first = len(sink.rows)

    # Simulate the cursor having been lost while the rows survived.
    sink.cursors.pop("org-a")
    second = _run(exporter.export_org("org-a"))

    assert len(sink.rows) == rows_after_first
    assert second.exported == 0, "no row was inserted twice"


def test_the_durable_total_counts_the_page_not_the_insert_rowcount() -> None:
    """After a re-read the records ARE durable -- the previous attempt committed them -- so
    counting only newly inserted rows would under-state the durable total."""
    stream, sink = FakeStream(), FakeSink()
    stream.add(10)
    exporter = _exporter(stream, sink)
    _run(exporter.export_org("org-a"))
    sink.cursors.pop("org-a")

    second = _run(exporter.export_org("org-a"))

    assert second.exported == 0
    assert second.cursor.durable_records == 10


# --- paging and isolation -------------------------------------------------------------------------


def test_pages_are_bounded_so_one_tenants_backlog_is_not_everyones() -> None:
    stream, sink = FakeStream(), FakeSink()
    stream.add(1_000)
    exporter = _exporter(stream, sink, page=100, max_pages=3)

    export = _run(exporter.export_org("org-a"))

    assert export.pages == 3
    assert export.exported == 300


def test_a_truncated_round_lower_bounds_completeness_rather_than_over_stating_it() -> None:
    """A ratio that over-states completeness is the M3 defect. When the pending count is unknown
    the stream's length is its upper bound, which makes the ratio a lower bound."""
    stream, sink = FakeStream(), FakeSink()
    stream.add(1_000)
    exporter = _exporter(stream, sink, page=100, max_pages=2)

    export = _run(exporter.export_org("org-a"))
    ratio = RoundSummary((export,)).completeness_ratio

    assert export.cursor.durable_records == 200
    assert ratio < 1.0
    assert ratio <= 200 / 1_000 + 0.001


def test_a_tenants_failure_does_not_abort_the_round() -> None:
    """Per-tenant isolation, like the re-hydrator's: one kind's failure never stalls another's."""
    stream, sink = FakeStream(), FakeSink()
    stream.add(5)
    exporter = _exporter(stream, sink)
    sink.fail_commit = True

    summary = _run(exporter.round_once(["org-a", "org-b"]))

    assert summary.failures >= 1
    assert len(summary.exports) == 2
    assert all(export.error != "" for export in summary.exports)


def test_a_round_sums_what_its_tenants_did() -> None:
    stream, sink = FakeStream(), FakeSink()
    stream.add(10)
    exporter = _exporter(stream, sink)

    summary = _run(exporter.round_once(["org-a"]))

    assert summary.exported == 10
    assert summary.lost == 0
    assert summary.failures == 0


def test_an_empty_round_is_complete() -> None:
    assert RoundSummary().completeness_ratio == 1.0
    assert RoundSummary().exported == 0


def test_nonsensical_paging_is_refused() -> None:
    stream, sink = FakeStream(), FakeSink()
    with pytest.raises(ValueError, match="page must be positive"):
        AuditExporter(stream, sink, page=0)
    with pytest.raises(ValueError, match="max_pages must be positive"):
        AuditExporter(stream, sink, max_pages=0)


# --- the acknowledged report is a push, not a read ------------------------------------------------


def test_the_acknowledged_total_is_pushed_because_the_store_forgets() -> None:
    """Once records are trimmed nothing in the store remembers they existed -- and that is
    exactly the number the subtraction needs. The writer counted them when it appended them."""
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    stream.add(10)
    exporter.note_acknowledged("org-a", 10)
    stream.trim_to(0)

    export = _run(exporter.export_org("org-a"))

    assert export.lost == 10
    assert export.cursor.records_lost == 10


def test_a_replayed_acknowledged_total_does_not_invent_loss() -> None:
    """A total is idempotent under a retry, where a delta applied twice would invent loss."""
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    stream.add(10)
    exporter.note_acknowledged("org-a", 10)
    exporter.note_acknowledged("org-a", 10)
    stream.trim_to(0)

    first = _run(exporter.export_org("org-a"))
    second = _run(exporter.export_org("org-a"))

    assert first.cursor.records_lost == 10
    assert second.cursor.records_lost == 10, "the total must not accumulate on a second round"
    assert second.lost == 0, "and the round must report no NEW loss"


def test_the_acknowledged_total_only_rises() -> None:
    stream, sink = FakeStream(), FakeSink()
    exporter = _exporter(stream, sink)
    exporter.note_acknowledged("org-a", 100)
    exporter.note_acknowledged("org-a", 5)
    stream.add(1)

    export = _run(exporter.export_org("org-a"))

    assert export.cursor.acknowledged_records == 100


def test_the_sink_reports_lifetime_acknowledged_counts_per_tenant() -> None:
    """The writer's side of the handshake, asserted against the real sink rather than a stub."""
    import asyncio as _asyncio

    from gateway_v2.audit.budget import StreamBudget
    from gateway_v2.audit.record import AuditPhase, AuditRecord
    from gateway_v2.audit.sink import AuditSink
    from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
    from gateway_v2.runtime.store_audit import AuditBatchResult

    class Store:
        async def append(self, records, maxlens):  # type: ignore[no-untyped-def]
            by_org: dict[str, int] = {}
            for org, _ in records:
                by_org[org] = by_org.get(org, 0) + 1
            return AuditBatchResult(
                written=len(records), trimmed=0, written_by_org=by_org,
            )

    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 2)
    audit = AuditSink(Store(), budget, queue_depth=100)
    for index in range(5):
        audit.emit(
            AuditRecord(
                request_id=f"r{index}",
                org_id="org-a" if index % 2 else "org-b",
                phase=AuditPhase.INPUT,
                outcome="allow",
                recorded_at_ns=index,
            ),
        )
    _asyncio.run(audit.drain(1.0))

    assert audit.acknowledged_by_org() == {"org-a": 2, "org-b": 3}


# --- the behind-ness reading ----------------------------------------------------------------------


def test_the_export_backlog_is_visible_and_is_not_loss() -> None:
    state = OrgCursor(
        org_id="org-a", durable_records=600, acknowledged_records=1_000, records_lost=100,
    )

    assert state.behind == 300
    assert state.records_lost == 100


def test_the_backlog_never_reads_negative() -> None:
    state = OrgCursor(org_id="org-a", durable_records=10, acknowledged_records=0)

    assert state.behind == 0
