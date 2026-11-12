"""F1a-2 evidence: what the MODEL source and state reads cost in Postgres at 100k orgs x 5 models.

Run (password from the environment only; nothing is written to disk but this script's output):

    export BENCH_PG_DSN="postgresql://postgres:$PGPASSWORD@127.0.0.1:55432/postgres"
    flock /home/claude/work/.ctl-db.lock nice -n 10 ../../venv-ci/bin/python -I pg_source_bench.py

It creates a SCRATCH database `f1a2_bench` (never control's `aimesh`), loads a synthetic estate
shaped like control's tables, measures, and drops the database. Tables mirror the columns the
projector would read (`core/models.py:1571-1683` `LLMModelConfig`, `auth_api_organization`,
`core_firewallconfig`) and the state record table (`state_control/schema.py:46-60`).

Every read runs the way the projector runs it: one read-only REPEATABLE READ transaction with the
session bound `statement_timeout = 800 ms` (`state_control/service.py:71`). Times are client wall
clock (the host is shared and loaded) plus the server's own `EXPLAIN ANALYZE` execution time,
which excludes the transfer.

Measured:
  1. the naive per-round full read of every model row (HEAD's projector pattern, `source_pg.py`)
  2. the design's per-org revision read, and the changed-org row fetch (1k and 2k orgs)
  3. the naive per-round snapshot of every MODEL state record (HEAD's `projector.py:220` pattern
     and the re-hydrator's deep `verify`, `rehydrate.py:349`)
  4. the index-only read the design uses instead (key, feed_seq) and the memo seed (key, hash)
  5. the cost of the revision triggers on a console write
"""

from __future__ import annotations

import os
import random
import sys
import time

import psycopg

DSN = os.environ["BENCH_PG_DSN"]
DB = "f1a2_bench"
ORGS, PER_ORG = 100_000, 5
BOUND_MS = 800

SETUP = f"""
CREATE TABLE org (id bigint PRIMARY KEY, slug text UNIQUE NOT NULL, is_active boolean NOT NULL);
INSERT INTO org SELECT g, 'org-' || lpad(g::text, 6, '0'), true FROM generate_series(1, {ORGS}) g;

CREATE TABLE llm (
  id bigserial PRIMARY KEY,
  organization_id bigint REFERENCES org(id) ON DELETE CASCADE,
  provider varchar(32) NOT NULL, model_name varchar(128) NOT NULL, model_id varchar(256) NOT NULL,
  api_key_env_var varchar(128) NOT NULL DEFAULT '', encrypted_api_key text NOT NULL DEFAULT '',
  api_key_last4 varchar(8) NOT NULL DEFAULT '', api_base varchar(512) NOT NULL DEFAULT '',
  region varchar(64) NOT NULL DEFAULT '', is_active boolean NOT NULL DEFAULT true,
  data_sensitivity_level varchar(32) NOT NULL DEFAULT 'public', compliance_tags jsonb NOT NULL DEFAULT '[]',
  cost_per_1k_input_tokens numeric(10,6) NOT NULL DEFAULT 0, cost_per_1k_output_tokens numeric(10,6) NOT NULL DEFAULT 0,
  latency_sla_ms integer NOT NULL DEFAULT 30000, risk_score double precision NOT NULL DEFAULT 0,
  routing_priority integer NOT NULL DEFAULT 0, rate_limit_rpm integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, model_name)
);
CREATE INDEX llm_org ON llm (organization_id);
INSERT INTO llm (organization_id, provider, model_name, model_id, encrypted_api_key, api_key_last4,
                 api_base, region, data_sensitivity_level, compliance_tags)
SELECT o, (ARRAY['openai','anthropic','azure','google','aws_bedrock'])[1 + (o + i) % 5],
       'model-' || i, 'openai/model-' || i || '-2024-08-06',
       'gAAAAAB' || md5(o::text || i) || md5(i::text || o) || md5(o::text) || md5(i::text) || '==',
       'abcd', CASE WHEN (o + i) % 5 = 2 THEN 'https://acme-' || o || '.openai.azure.com' ELSE '' END,
       CASE WHEN (o + i) % 5 = 4 THEN 'us-east-1' ELSE '' END,
       'confidential', '["SOC2", "HIPAA"]'
FROM generate_series(1, {ORGS}) o, generate_series(0, {PER_ORG - 1}) i;

CREATE TABLE fwc (organization_id bigint UNIQUE REFERENCES org(id), allowed_models text NOT NULL DEFAULT '',
                  default_model varchar(64) NOT NULL DEFAULT '');
INSERT INTO fwc SELECT g, '', '' FROM generate_series(1, {ORGS}) g;

-- the design: a per-org source revision, maintained by triggers in control's database
CREATE TABLE amf_model_source_rev (
  org_id bigint PRIMARY KEY, rev bigint NOT NULL DEFAULT 1, changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO amf_model_source_rev (org_id) SELECT id FROM org;
CREATE FUNCTION amf_model_bump(org bigint) RETURNS void LANGUAGE sql AS $$
  INSERT INTO amf_model_source_rev AS r (org_id) VALUES (org)
  ON CONFLICT (org_id) DO UPDATE SET rev = r.rev + 1, changed_at = clock_timestamp();
$$;
CREATE FUNCTION amf_model_bump_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.organization_id IS NOT NULL THEN PERFORM amf_model_bump(OLD.organization_id); END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.organization_id IS NOT NULL
     AND (TG_OP = 'INSERT' OR NEW.organization_id IS DISTINCT FROM OLD.organization_id) THEN
    PERFORM amf_model_bump(NEW.organization_id);
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER llm_rev AFTER INSERT OR UPDATE OR DELETE ON llm FOR EACH ROW EXECUTE FUNCTION amf_model_bump_row();

CREATE TABLE amf_state_record (
  kind text NOT NULL, key text NOT NULL, epoch bigint NOT NULL, seq bigint NOT NULL, feed_seq bigint NOT NULL,
  content_hash text NOT NULL, deleted boolean NOT NULL, op text NOT NULL, signature text NOT NULL,
  body text NOT NULL, engaged boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, key)
);
CREATE INDEX amf_state_record_feed ON amf_state_record (kind, feed_seq);
INSERT INTO amf_state_record
SELECT 'model', 'org-' || lpad(g::text, 6, '0'), 1, g, g, md5(g::text) || md5(g::text), false, 'put',
       md5(g::text) || md5((g + 1)::text), repeat(md5(g::text), 64) || '...', false, now()
FROM generate_series(1, {ORGS}) g;
ANALYZE;
"""

NAIVE_SOURCE = (
    "SELECT o.slug, m.id, m.provider, m.model_name, m.model_id, m.api_base, m.region, m.is_active, "
    "m.encrypted_api_key, m.api_key_env_var, m.data_sensitivity_level, m.compliance_tags, "
    "extract(epoch FROM m.created_at)::bigint FROM llm m JOIN org o ON o.id = m.organization_id "
    "ORDER BY o.slug, m.id"
)
REVS = "SELECT org_id, rev FROM amf_model_source_rev"
CHANGED = (
    "SELECT o.slug, m.id, m.provider, m.model_name, m.model_id, m.api_base, m.region, m.is_active, "
    "m.encrypted_api_key, m.api_key_env_var, m.data_sensitivity_level, m.compliance_tags, "
    "extract(epoch FROM m.created_at)::bigint FROM llm m JOIN org o ON o.id = m.organization_id "
    "WHERE m.organization_id = ANY(%s) ORDER BY o.slug, m.id"
)
SNAPSHOT = (
    "SELECT kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body "
    "FROM amf_state_record WHERE kind = 'model' ORDER BY feed_seq"
)
INDEX = "SELECT key, feed_seq FROM amf_state_record WHERE kind = 'model'"
MEMO_SEED = "SELECT key, content_hash FROM amf_state_record WHERE kind = 'model'"


def bounded_read(conn: psycopg.Connection, sql: str, params: object = None, *, bound: bool = True) -> str:
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if bound:
        conn.execute(f"SET LOCAL statement_timeout = {BOUND_MS}")
    t0 = time.perf_counter()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    except psycopg.errors.QueryCanceled:
        conn.execute("ROLLBACK")
        return f"CANCELLED by statement_timeout={BOUND_MS} ms after {(time.perf_counter() - t0) * 1000:,.0f} ms"
    wall = (time.perf_counter() - t0) * 1000
    size = sum(len(str(v)) for row in rows[:2000] for v in row) / max(min(len(rows), 2000), 1) * len(rows)
    conn.execute("ROLLBACK")
    return f"{len(rows):,} rows, ~{size / 1e6:,.1f} MB as text, {wall:,.0f} ms wall"


def server_ms(conn: psycopg.Connection, sql: str, params: object = None) -> str:
    try:
        plan = conn.execute(f"EXPLAIN (ANALYZE, TIMING OFF) {sql}", params).fetchall()
    except psycopg.Error as exc:
        return f"n/a ({type(exc).__name__})"
    for (line,) in plan:
        if line.startswith("Execution Time"):
            return line.split(":", 1)[1].strip()
    return "?"


def main() -> int:
    print(f"python={sys.version.split()[0]} psycopg={psycopg.__version__} orgs={ORGS:,} rows={ORGS * PER_ORG:,}")
    with psycopg.connect(DSN, autocommit=True) as admin:
        print(admin.execute("SELECT version()").fetchone()[0])  # type: ignore[index]
        admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        admin.execute(f"CREATE DATABASE {DB}")
    dsn = DSN.rsplit("/", 1)[0] + "/" + DB
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            t0 = time.perf_counter()
            for stmt in SETUP.split(";\n\n"):
                conn.execute(stmt)  # type: ignore[arg-type]
            print(f"setup: {time.perf_counter() - t0:,.0f} s")
            size = conn.execute("SELECT pg_size_pretty(pg_relation_size('llm')), pg_size_pretty(pg_total_relation_size('amf_state_record'))").fetchone()
            print(f"table sizes: llm={size[0]} amf_state_record(model)={size[1]}")  # type: ignore[index]
            rng = random.Random(1)
            some = [rng.randint(1, ORGS) for _ in range(2000)]
            print("\n| read | per round | result under the 800 ms bound | server execution |")
            print("|---|---|---|---|")
            for label, sql, params, when in (
                ("naive: every model row (HEAD pattern)", NAIVE_SOURCE, None, "every round"),
                ("design: per-org revisions", REVS, None, "every round"),
                ("design: rows of 1,000 changed orgs", CHANGED, (some[:1000],), "on change"),
                ("design: rows of 2,000 changed orgs", CHANGED, (some,), "on change"),
                ("naive: every MODEL state record (HEAD projector.py:220, verify rehydrate.py:349)", SNAPSHOT, None, "every round / deep"),
                ("design: index only (key, feed_seq)", INDEX, None, "deep verify"),
                ("design: memo seed (key, content_hash)", MEMO_SEED, None, "projector start"),
            ):
                for _ in range(2):  # warm the cache once; report the second run
                    result = bounded_read(conn, sql, params)
                print(f"| {label} | {when} | {result} | {server_ms(conn, sql, params)} |")
            print("\nunbounded naive reads (how long they really take):")
            for label, sql in (("every model row", NAIVE_SOURCE), ("every MODEL record", SNAPSHOT)):
                print(f"  {label}: {bounded_read(conn, sql, bound=False)}")

            print("\nconsole write cost with and without the revision trigger (autocommit, 2,000 updates):")
            ids = [rng.randint(1, ORGS * PER_ORG) for _ in range(2000)]
            for state in ("ENABLE", "DISABLE", "ENABLE"):
                conn.execute(f"ALTER TABLE llm {state} TRIGGER llm_rev")
                t0 = time.perf_counter()
                for i in ids:
                    conn.execute("UPDATE llm SET is_active = NOT is_active, updated_at = now() WHERE id = %s", (i,))
                per = (time.perf_counter() - t0) / len(ids) * 1000
                print(f"  trigger {state.lower()}d: {per:.3f} ms per update (wall, includes round trip)")
            revs = conn.execute("SELECT count(*) FROM amf_model_source_rev WHERE rev > 1").fetchone()[0]  # type: ignore[index]
            touched = conn.execute(
                "SELECT count(DISTINCT organization_id) FROM llm WHERE id = ANY(%s)", (ids,),
            ).fetchone()[0]  # type: ignore[index]
            print(f"  orgs whose revision moved: {revs:,}; distinct orgs of the updated rows: {touched:,}")
            assert revs == touched, "a trigger missed an org (or bumped one it should not have)"
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        print("scratch database dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
