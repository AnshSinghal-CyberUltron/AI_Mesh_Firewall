"""The exporter: drain each tenant's stream into the durable sink, and count what it loses.

This is the half of GW14c that answers R2-11's M3 finding — *a flush erased 36% of the audit
records while `audit_completeness_ratio` read 1.0*. The budget in `gateway_v2/audit/` keeps audit
from evicting security state; it does so BY TRIMMING, which means records leave the store. Whether
that is retention or loss depends on one thing only: whether anything durable had them first.

So each round, per tenant:

1. read the store's head and oldest surviving id;
2. decide whether the trim overtook the durable cursor — if it did, that is loss, counted;
3. read a bounded page above the durable cursor;
4. commit the page and the advanced cursor in ONE transaction.

Order matters. Loss is assessed BEFORE the page is read, because reading first and then noticing
the gap would attribute the gap to the records that happened to survive. And the cursor only ever
advances over records the sink actually took, so a crash between the two re-reads a page it has
already stored and inserts nothing.

`audit_completeness_ratio` is computed here and nowhere else, from `durable / acknowledged`. The
sink's `acknowledged_ratio` is a different number with a different name, and keeping them apart is
the fix.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from audit_control.cursor import OrgCursor, StreamId, parse_id, total_loss
from audit_control.sink_pg import DurableRecord

LOG = logging.getLogger("amf.audit.export")

DEFAULT_PAGE = 512
"""Records per page. Bounded so one tenant's backlog cannot monopolise a round."""

MAX_PAGES_PER_ROUND = 64
"""Pages one tenant may drain in one round before the others get a turn.

A tenant that has been offline for an hour has a long backlog, and draining it to completion
before touching anyone else would convert one tenant's backlog into everyone's export lag.
"""


class AuditStreamReader(Protocol):
    """What the exporter needs from the store. `ValkeyAuditStore` satisfies it."""

    async def read(
        self,
        org: str,
        *,
        after: str = ...,
        limit: int = ...,
    ) -> tuple[tuple[str, bytes], ...]:
        ...

    async def first_id(self, org: str) -> str | None:
        ...

    async def length(self, org: str) -> int:
        ...


class DurableSink(Protocol):
    def cursor_for(self, org_id: str) -> OrgCursor:
        ...

    def commit_page(
        self,
        records: Sequence[DurableRecord],
        cursor_state: OrgCursor,
    ) -> int:
        ...


@dataclass(frozen=True, slots=True)
class OrgExport:
    """What one tenant's export did this round."""

    org_id: str
    exported: int
    lost: int
    pages: int
    cursor: OrgCursor
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class RoundSummary:
    """What a whole round did. Per-tenant isolation: one failure never aborts the others."""

    exports: tuple[OrgExport, ...] = ()

    @property
    def exported(self) -> int:
        return sum(export.exported for export in self.exports)

    @property
    def lost(self) -> int:
        return sum(export.lost for export in self.exports)

    @property
    def failures(self) -> int:
        return sum(1 for export in self.exports if not export.ok)

    @property
    def durable_records(self) -> int:
        return sum(export.cursor.durable_records for export in self.exports)

    @property
    def acknowledged_records(self) -> int:
        return sum(export.cursor.acknowledged_records for export in self.exports)

    @property
    def completeness_ratio(self) -> float:
        """THE `audit_completeness_ratio`. Durable over acknowledged, never 1.0 by assumption."""
        reference = self.acknowledged_records
        return self.durable_records / reference if reference else 1.0


class AuditExporter:
    """Drains audit streams into the durable sink. One instance per process."""

    def __init__(
        self,
        reader: AuditStreamReader,
        sink: DurableSink,
        *,
        page: int = DEFAULT_PAGE,
        max_pages: int = MAX_PAGES_PER_ROUND,
    ) -> None:
        if page <= 0:
            raise ValueError("page must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self._reader = reader
        self._sink = sink
        self._page = page
        self._max_pages = max_pages
        self._acknowledged: dict[str, int] = {}

    def note_acknowledged(self, org_id: str, lifetime_records: int) -> None:
        """Tell the exporter how many records the WRITER has appended for this tenant, ever.

        The exporter cannot derive this. Once records are trimmed they are gone, so nothing in
        the store remembers that they existed — and that is precisely the number the subtraction
        needs. The writer counted them when it appended them, so this is a push.

        It is a lifetime TOTAL rather than a delta, and it only ever rises: a total is idempotent
        under a retry, where a delta applied twice would invent loss that never happened.
        """
        if lifetime_records > self._acknowledged.get(org_id, 0):
            self._acknowledged[org_id] = lifetime_records

    async def export_org(self, org_id: str) -> OrgExport:
        """One tenant, up to `max_pages` pages. Reports a failure rather than raising."""
        try:
            return await self._export_org(org_id)
        except Exception as exc:  # per-tenant isolation: one failure must not abort the round
            LOG.warning(
                "audit_export_failed org=%s error=%s",
                org_id,
                f"{type(exc).__name__}: {exc}"[:300],
            )
            return OrgExport(
                org_id=org_id,
                exported=0,
                lost=0,
                pages=0,
                cursor=self._sink.cursor_for(org_id),
                error=f"{type(exc).__name__}",
            )

    async def _export_org(self, org_id: str) -> OrgExport:
        state = self._sink.cursor_for(org_id)
        before_lost = state.records_lost

        # A trim may have overtaken the cursor. Move it to just below the oldest surviving record
        # FIRST: a cursor that keeps asking for records which no longer exist never advances
        # again, and the loss would become permanent silence instead of one counted event.
        oldest_raw = await self._reader.first_id(org_id)
        oldest = parse_id(oldest_raw) if oldest_raw else None
        if oldest is not None and oldest > state.durable:
            state = replace(state, durable=_predecessor(oldest))

        exported = 0
        pages = 0
        drained = False
        while pages < self._max_pages:
            entries = await self._reader.read(
                org_id, after=str(state.durable), limit=self._page,
            )
            if not entries:
                drained = True
                break
            records = [
                DurableRecord(org_id=org_id, stream_id=parse_id(raw_id), payload=payload)
                for raw_id, payload in entries
            ]
            advanced = _advance(state, records)
            # 4. The page and the cursor commit together, or neither does.
            exported += self._sink.commit_page(records, advanced)
            state = advanced
            pages += 1
            if len(entries) < self._page:
                drained = True
                break

        # Now the subtraction. `pending` is what is still in the stream above the durable cursor:
        # after a complete drain that is zero by construction, so the answer is exact. When the
        # round was TRUNCATED the stream's length is used as an upper bound on pending, which
        # makes the loss a LOWER bound and the completeness ratio a lower bound too -- the safe
        # direction, since a ratio that over-states completeness is the M3 defect itself.
        pending = 0 if drained else await self._reader.length(org_id)
        acknowledged = max(
            self._acknowledged.get(org_id, 0),
            state.durable_records + state.records_lost + pending,
        )
        state = replace(
            state,
            acknowledged_records=acknowledged,
            records_lost=total_loss(acknowledged, state.durable_records, pending),
        )
        lost = state.records_lost - before_lost
        if lost > 0:
            LOG.warning(
                "audit_records_lost org=%s new=%d total=%d acknowledged=%d durable=%d "
                "pending=%d: the budget trimmed records the exporter had not made durable. "
                "Audit is being LOST, not retained -- the export is not draining faster than "
                "the trim rate",
                org_id,
                lost,
                state.records_lost,
                acknowledged,
                state.durable_records,
                pending,
            )
            # Persist the loss. It was computed after the last page committed, so without this
            # the number would be recomputed from scratch every round and never be durable.
            self._sink.commit_page((), state)
        return OrgExport(
            org_id=org_id, exported=exported, lost=lost, pages=pages, cursor=state,
        )

    async def round_once(self, orgs: Sequence[str]) -> RoundSummary:
        """One round over the named tenants. Isolated per tenant, like the re-hydrator's."""
        return RoundSummary(tuple([await self.export_org(org) for org in orgs]))


def _predecessor(stream_id: StreamId) -> StreamId:
    """The position immediately below an id, so an exclusive read still returns it.

    The exporter's page read is exclusive (`XRANGE (cursor`), because the cursor names a record
    already durable. After a trim the oldest surviving record is NOT durable, so the cursor has
    to sit just below it rather than on it — otherwise the first record after every trim is
    skipped, which would turn a visible loss into a silent one.
    """
    if stream_id.seq > 0:
        return StreamId(stream_id.ms, stream_id.seq - 1)
    return StreamId(max(stream_id.ms - 1, 0), 0)


def _advance(state: OrgCursor, records: Sequence[DurableRecord]) -> OrgCursor:
    """Move the cursor past a page: the position, the durable total, and the acknowledged head.

    The total advances by the PAGE SIZE rather than by the rows the insert added. The two differ
    only when a page is being re-read after a crash, and in that case the records really are
    durable — the previous attempt committed them — so counting them again as newly inserted
    would under-state the durable total while counting them as not durable would be false.
    Double insertion is prevented by the primary key, not by this arithmetic.
    """
    last = records[-1].stream_id
    return replace(
        state,
        durable=last,
        acknowledged=max(state.acknowledged, last),
        durable_records=state.durable_records + len(records),
    )
