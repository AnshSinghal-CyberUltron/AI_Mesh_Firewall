"""Control-plane schema. Postgres is the source of truth; the store holds a published copy.

Three tables, and two columns that exist because of R2-02:

* `feed_seq` on both the counter and the record. It is the cursor space: per kind, advancing on
  every write, never resetting — a signed rollback writes `(epoch + 1, 0)`, so `seq` moves
  backwards and a cursor built on it would skip the rollback forever.
* `count` and `on_count` on the counter row, maintained inside the write transaction. They are
  what let a reader prove the published copy is complete in O(1) instead of by digesting every
  record. Computing them with `SELECT count(*)` per write would put the O(records) scan back.

`body` is **text, not jsonb**. The signature covers the record's canonical bytes, and jsonb
normalises whitespace and key order on write, so a jsonb round trip would silently invalidate
every signature. This is the one place where the obvious column type is the wrong one.

There is no migration from RC2's `rv2_*` tables: no v3 control-plane schema has ever been
deployed, so this is the initial DDL rather than an alteration. The additive `ALTER` list in the
GW05c plan (§17) applies only to someone porting the RC2 prototype.
"""

from __future__ import annotations

RECORD_COLUMNS = (
    "kind",
    "key",
    "epoch",
    "seq",
    "feed_seq",
    "content_hash",
    "deleted",
    "op",
    "signature",
    "body",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS amf_state_counter (
  kind      text   PRIMARY KEY,
  epoch     bigint NOT NULL,
  seq       bigint NOT NULL,
  feed_seq  bigint NOT NULL DEFAULT 0,
  count     bigint NOT NULL DEFAULT 0,
  on_count  bigint NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS amf_state_record (
  kind         text    NOT NULL,
  key          text    NOT NULL,
  epoch        bigint  NOT NULL,
  seq          bigint  NOT NULL,
  feed_seq     bigint  NOT NULL,
  content_hash text    NOT NULL,
  deleted      boolean NOT NULL,
  op           text    NOT NULL,
  signature    text    NOT NULL,
  body         text    NOT NULL,
  engaged      boolean NOT NULL DEFAULT false,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, key)
);

-- "changed since position P" for the re-hydrator, without a whole-kind scan.
CREATE INDEX IF NOT EXISTS amf_state_record_feed
  ON amf_state_record (kind, feed_seq);

CREATE TABLE IF NOT EXISTS amf_state_log (
  id           bigserial PRIMARY KEY,
  kind         text    NOT NULL,
  key          text    NOT NULL,
  epoch        bigint  NOT NULL,
  seq          bigint  NOT NULL,
  feed_seq     bigint  NOT NULL,
  content_hash text    NOT NULL,
  deleted      boolean NOT NULL,
  op           text    NOT NULL,
  signature    text    NOT NULL,
  body         text    NOT NULL,
  actor        text    NOT NULL,
  at           timestamptz NOT NULL DEFAULT now()
);
"""
