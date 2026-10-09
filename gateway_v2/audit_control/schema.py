"""The durable audit schema. Append-only, idempotent, and carrying its own cursor.

Two tables:

* `amf_audit_record` — the records themselves. The primary key is `(org_id, stream_id)`, the
  store's own id, which makes the insert IDEMPOTENT: an exporter that commits to Postgres and
  then dies before advancing its cursor re-reads the same page and inserts nothing. Without
  that, every restart would double-count and `records_lost` would be arithmetic on a number
  nobody could trust.
* `amf_audit_cursor` — one row per org: the durable high-water mark, the acknowledged one as
  last seen in the store, and the running `records_lost`. It lives in the SAME database as the
  records, so advancing the cursor and inserting the page commit together. A cursor in the store
  (or in RAM) could advance past a page that was never committed, which is precisely the class of
  gap this card exists to make visible.

`body` is **text, not jsonb**, for a weaker version of the reason `state_control/schema.py` gives:
nothing signs an audit record, but jsonb normalises key order and whitespace, so a round trip
would change the bytes the byte model sized and the exporter counted. Keeping the exact bytes
means a durable record is comparable to the one that was in the store.

`stream_id` is text because a Redis stream id is `<millis>-<seq>`, which is two integers in a
trench coat. Comparing them needs the split, so `id_sort` carries the parsed pair and the index
orders on it — string ordering of `10-1` and `9-1` is wrong in exactly the way that would make
loss detection silently skip records.
"""

from __future__ import annotations

RECORD_COLUMNS = (
    "org_id",
    "stream_id",
    "id_ms",
    "id_seq",
    "request_id",
    "phase",
    "outcome",
    "schema_version",
    "recorded_at_ns",
    "body",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS amf_audit_record (
  org_id         text   NOT NULL,
  stream_id      text   NOT NULL,
  id_ms          bigint NOT NULL,
  id_seq         bigint NOT NULL,
  request_id     text   NOT NULL,
  phase          text   NOT NULL,
  outcome        text   NOT NULL,
  schema_version int    NOT NULL,
  recorded_at_ns bigint NOT NULL,
  body           text   NOT NULL,
  durable_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, stream_id)
);

-- "what did this tenant do, in order" without a scan, and the ordering is on the PARSED id
-- rather than on its text, because '10-1' sorts below '9-1' as a string.
CREATE INDEX IF NOT EXISTS amf_audit_record_org_order
  ON amf_audit_record (org_id, id_ms, id_seq);

-- The join handle a customer actually has: the request id from their SDK error.
CREATE INDEX IF NOT EXISTS amf_audit_record_request
  ON amf_audit_record (request_id);

CREATE TABLE IF NOT EXISTS amf_audit_cursor (
  org_id              text   PRIMARY KEY,
  durable_id          text   NOT NULL,
  durable_ms          bigint NOT NULL,
  durable_seq         bigint NOT NULL,
  durable_records     bigint NOT NULL DEFAULT 0,
  acknowledged_id     text   NOT NULL DEFAULT '0-0',
  acknowledged_records bigint NOT NULL DEFAULT 0,
  records_lost        bigint NOT NULL DEFAULT 0,
  updated_at          timestamptz NOT NULL DEFAULT now()
);
"""
