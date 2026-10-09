"""The Postgres durable sink: an idempotent page insert and a cursor that commits with it.

The one invariant worth stating on its own, because everything else here serves it:

    the page and the cursor advance in ONE transaction, or neither does.

If the insert committed and the cursor did not, a restart would re-read the page — which is safe,
because `ON CONFLICT DO NOTHING` on `(org_id, stream_id)` makes the insert idempotent. If the
cursor committed and the insert did not, the records would be skipped and `records_lost` would
read zero while they were gone. So the cursor lives in the same database as the records and moves
in the same transaction, and the asymmetry is deliberately on the safe side.

Session bounds come from `state_control.pg.PostgresControlDB.cursor_tx()`, so the four timeouts
R2-04 verified — and `verify_bounds()` with them — cover this sink too rather than being
re-declared here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from audit_control.cursor import ZERO_ID, OrgCursor, StreamId, parse_id
from audit_control.schema import SCHEMA

LOG = logging.getLogger("amf.audit.sink_pg")


class BoundedDatabase(Protocol):
    """What the sink needs: a bounded transaction yielding a cursor. `PostgresControlDB` is one."""

    def cursor_tx(self) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class DurableRecord:
    """One record on its way into the sink, with its store position already parsed."""

    org_id: str
    stream_id: StreamId
    payload: bytes

    def columns(self) -> tuple[object, ...]:
        """Flatten for the insert. The body stays the EXACT bytes the store held.

        A malformed payload is still stored. An audit record that cannot be parsed is itself a
        finding, and dropping it here would mean the one record that proves something went wrong
        is the one record that does not survive.
        """
        try:
            body = json.loads(self.payload)
        except (ValueError, UnicodeDecodeError):
            body = {}
        fields = body if isinstance(body, dict) else {}
        return (
            self.org_id,
            str(self.stream_id),
            self.stream_id.ms,
            self.stream_id.seq,
            str(fields.get("request_id", "")),
            str(fields.get("phase", "")),
            str(fields.get("outcome", "")),
            int(fields.get("v", 0) or 0),
            int(fields.get("at_ns", 0) or 0),
            self.payload.decode("utf-8", "replace"),
        )


_INSERT = """
INSERT INTO amf_audit_record
  (org_id, stream_id, id_ms, id_seq, request_id, phase, outcome, schema_version,
   recorded_at_ns, body)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id, stream_id) DO NOTHING
"""

_UPSERT_CURSOR = """
INSERT INTO amf_audit_cursor
  (org_id, durable_id, durable_ms, durable_seq, durable_records,
   acknowledged_id, acknowledged_records, records_lost)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (org_id) DO UPDATE SET
  durable_id = EXCLUDED.durable_id,
  durable_ms = EXCLUDED.durable_ms,
  durable_seq = EXCLUDED.durable_seq,
  durable_records = EXCLUDED.durable_records,
  acknowledged_id = EXCLUDED.acknowledged_id,
  acknowledged_records = EXCLUDED.acknowledged_records,
  records_lost = EXCLUDED.records_lost,
  updated_at = now()
"""

_SELECT_CURSOR = """
SELECT durable_id, durable_records, acknowledged_id, acknowledged_records, records_lost
FROM amf_audit_cursor WHERE org_id = %s
"""

_SELECT_ALL_CURSORS = """
SELECT org_id, durable_id, durable_records, acknowledged_id, acknowledged_records, records_lost
FROM amf_audit_cursor ORDER BY org_id
"""


class PostgresAuditSink:
    """The durable sink. Every method is one bounded transaction."""

    def __init__(self, database: BoundedDatabase) -> None:
        self._db = database

    def init_schema(self) -> None:
        with self._db.cursor_tx() as cursor:
            cursor.execute(SCHEMA)

    def cursor_for(self, org_id: str) -> OrgCursor:
        """This tenant's position, or a fresh one at `0-0` if it has never been exported."""
        with self._db.cursor_tx() as cursor:
            cursor.execute(_SELECT_CURSOR, (org_id,))
            row = cursor.fetchone()
        if row is None:
            return OrgCursor(org_id=org_id)
        return OrgCursor(
            org_id=org_id,
            durable=parse_id(str(row[0])),
            durable_records=int(row[1]),
            acknowledged=parse_id(str(row[2])),
            acknowledged_records=int(row[3]),
            records_lost=int(row[4]),
        )

    def cursors(self) -> tuple[OrgCursor, ...]:
        """Every tenant's position. The exporter's metrics read; O(tenants), off any hot path."""
        with self._db.cursor_tx() as cursor:
            cursor.execute(_SELECT_ALL_CURSORS)
            rows = cursor.fetchall()
        return tuple(
            OrgCursor(
                org_id=str(row[0]),
                durable=parse_id(str(row[1])),
                durable_records=int(row[2]),
                acknowledged=parse_id(str(row[3])),
                acknowledged_records=int(row[4]),
                records_lost=int(row[5]),
            )
            for row in rows
        )

    def commit_page(
        self,
        records: Sequence[DurableRecord],
        cursor_state: OrgCursor,
    ) -> int:
        """Insert the page and advance the cursor, atomically. Returns rows actually inserted.

        The return value is the count of rows the insert added, which is NOT `len(records)` when
        a page is being re-read after a crash. Advancing `durable_records` by this number rather
        than by the page size is what keeps the durable total — and therefore
        `audit_completeness_ratio` — from inflating on every restart.
        """
        with self._db.cursor_tx() as tx:
            inserted = 0
            for record in records:
                tx.execute(_INSERT, record.columns())
                inserted += int(getattr(tx, "rowcount", 0) or 0)
            tx.execute(
                _UPSERT_CURSOR,
                (
                    cursor_state.org_id,
                    str(cursor_state.durable) if not cursor_state.durable.is_zero else ZERO_ID,
                    cursor_state.durable.ms,
                    cursor_state.durable.seq,
                    cursor_state.durable_records,
                    str(cursor_state.acknowledged),
                    cursor_state.acknowledged_records,
                    cursor_state.records_lost,
                ),
            )
        return inserted

    def durable_count(self, org_id: str) -> int:
        """Rows actually in the sink for this tenant. For the gate, not for the hot path."""
        with self._db.cursor_tx() as cursor:
            cursor.execute("SELECT count(*) FROM amf_audit_record WHERE org_id = %s", (org_id,))
            row = cursor.fetchone()
        return 0 if row is None else int(row[0])
