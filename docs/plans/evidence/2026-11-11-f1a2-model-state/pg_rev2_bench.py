"""F1a-2 revision-2 evidence: the mechanisms the rev-1 reviews asked for, on real Postgres 16 + Valkey.

Run from `gateway_v2/` (password from the environment only):

    export BENCH_PG_DSN="postgresql://postgres:$PGPASSWORD@127.0.0.1:55432/postgres"
    export BENCH_REDIS_URL="redis://127.0.0.1:56379/0"
    flock /home/claude/work/.heavy.lock flock /home/claude/work/.ctl-db.lock nice -n 10 \
        ../../venv-ci/bin/python -I -u ../docs/plans/evidence/2026-11-11-f1a2-model-state/pg_rev2_bench.py

Scratch database `f1a2_rev2` (dropped at the end) and Valkey namespace `{f1a2bench}` (keys
deleted at the end). Every projector-shaped read runs in one REPEATABLE READ READ ONLY
transaction with `statement_timeout = 800 ms` (`state_control/service.py:71`).

Sections
  A. Bounded source read (rev-2 §4.2.2): per-org LATERAL LIMIT 129, the token never selected
     (length via the TOAST header, shape checked server-side only under the cap), infinity-safe
     `created_at`, chunks packed by ROW budget. Against reviewer B's cases: an org with 250,000
     inactive rows, an org whose rows carry ~1.4 MB tokens, an `infinity` timestamp, and 64-model
     density.
  B. Bisection inside one snapshot with SAVEPOINTs: a statement error and a statement timeout
     planted on one org are isolated to that org; the other orgs are read in the same snapshot.
  C. Statement-level triggers with transition tables, a projected-column filter, org-id-ordered
     upserts, ENABLE ALWAYS: non-projected bulk update bumps 0 orgs; projected bumps each once;
     `session_replication_role = replica` still bumps; org delete removes the revision row; the
     projector's `pg_trigger.tgenabled` check; the cross-statement deadlock residual.
  D. State reads (rev-2 §4.3): keyset-paged full repair read, incremental STALE read by
     `(kind, feed_seq)`, targeted CONTENTS read; base-round slimming for PLAN/KEY at 100k
     (hash-only snapshot, revision-only plan source, full key source).
  E. Writer throughput on the real path (`StateWriter` + `PostgresControlDB` + `ValkeyPublisher`):
     `put_many` chunks of 100 with typical and 64-model bodies, and single `put(if_changed)`.
"""

from __future__ import annotations

import base64
import os
import random
import sys
import threading
import time

import psycopg

sys.path.insert(0, ".")

DSN = os.environ["BENCH_PG_DSN"]
REDIS_URL = os.environ["BENCH_REDIS_URL"]
DB = "f1a2_rev2"
NS = "{f1a2bench}"
ORGS = 100_000          # typical estate: 5 models per org
DENSE = 2_000           # dense estate: 64 models per org (ids ORGS+1 .. ORGS+DENSE)
BOUND_MS = 800
ROW_BUDGET = 2_000      # rows per chunk statement
POISON_TS = ORGS + DENSE + 1   # infinity created_at
FAT = ORGS + DENSE + 2         # 50 rows x ~1.4 MB tokens
WIDE = ORGS + DENSE + 3        # 250,000 inactive rows
ERR = ORGS + DENSE + 4         # planted statement error (division by zero)
SLOW = ORGS + DENSE + 5        # planted statement timeout (pg_sleep)

SETUP = f"""
CREATE TABLE org (id bigint PRIMARY KEY, slug text UNIQUE NOT NULL, is_active boolean NOT NULL);
INSERT INTO org SELECT g, 'org-' || lpad(g::text, 6, '0'), true FROM generate_series(1, {ORGS + DENSE + 5}) g;

CREATE TABLE llm (
  id bigserial PRIMARY KEY,
  organization_id bigint REFERENCES org(id) ON DELETE CASCADE,
  provider varchar(32) NOT NULL, model_name varchar(128) NOT NULL, model_id varchar(256) NOT NULL,
  api_key_env_var varchar(128) NOT NULL DEFAULT '', encrypted_api_key text NOT NULL DEFAULT '',
  api_base varchar(512) NOT NULL DEFAULT '', region varchar(64) NOT NULL DEFAULT '',
  is_active boolean NOT NULL DEFAULT true, data_sensitivity_level varchar(32) NOT NULL DEFAULT 'public',
  compliance_tags jsonb NOT NULL DEFAULT '[]', routing_priority integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llm_org ON llm (organization_id);
INSERT INTO llm (organization_id, provider, model_name, model_id, encrypted_api_key, api_base, region)
SELECT o, (ARRAY['openai','anthropic','azure','google','aws_bedrock'])[1 + (o + i) % 5],
       'model-' || i, 'openai/model-' || i || '-2024-08-06',
       'gAAAAAB' || md5(o::text || '/' || i) || md5(i::text || '/' || o) || md5(o::text) || md5(i::text) || md5((o*7)::text) || '==',
       CASE WHEN (o + i) % 5 = 2 THEN 'https://acme-' || o || '.openai.azure.com' ELSE '' END,
       CASE WHEN (o + i) % 5 = 4 THEN 'us-east-1' ELSE '' END
FROM generate_series(1, {ORGS}) o, generate_series(0, 4) i;
INSERT INTO llm (organization_id, provider, model_name, model_id, encrypted_api_key)
SELECT o, 'openai', 'dense-model-' || i, 'openai/dense-' || i,
       'gAAAAAB' || md5(o::text || '#' || i) || md5(i::text || '#' || o) || md5(o::text) || md5(i::text) || md5((o*3)::text) || '=='
FROM generate_series({ORGS + 1}, {ORGS + DENSE}) o, generate_series(0, 63) i;
INSERT INTO llm (organization_id, provider, model_name, model_id, encrypted_api_key, created_at)
VALUES ({POISON_TS}, 'openai', 'bad-ts', 'gpt-4o', 'gAAAAAB' || md5('x') || md5('y') || md5('z') || md5('w') || '==', 'infinity');
INSERT INTO llm (organization_id, provider, model_name, model_id, encrypted_api_key)
SELECT {FAT}, 'openai', 'fat-' || i, 'gpt-4o',
       'gAAAAAB' || (SELECT string_agg(md5(i::text || ':' || j::text), '') FROM generate_series(1, 44000) j)
FROM generate_series(1, 50) i;
INSERT INTO llm (organization_id, provider, model_name, model_id, is_active)
SELECT {WIDE}, 'openai', 'wide-' || i, 'gpt-4o', false FROM generate_series(1, 250000) i;
INSERT INTO llm (organization_id, provider, model_name, model_id) VALUES
  ({ERR}, 'openai', 'err', 'gpt-4o'), ({SLOW}, 'openai', 'slow', 'gpt-4o');

CREATE TABLE fwc (organization_id bigint UNIQUE REFERENCES org(id) ON DELETE CASCADE,
                  allowed_models text NOT NULL DEFAULT '', default_model varchar(64) NOT NULL DEFAULT '',
                  model_isolation_enabled boolean NOT NULL DEFAULT true);
INSERT INTO fwc (organization_id) SELECT id FROM org;

CREATE TABLE plan_doc (organization_id bigint PRIMARY KEY REFERENCES org(id) ON DELETE CASCADE, body text NOT NULL, revision int NOT NULL);
INSERT INTO plan_doc SELECT g, (SELECT string_agg(md5(g::text || '%' || j::text), '') FROM generate_series(1, 56) j), 1
FROM generate_series(1, {ORGS}) g;

CREATE TABLE gwkey (id uuid PRIMARY KEY, key_hash char(64) UNIQUE NOT NULL, organization_id bigint REFERENCES org(id) ON DELETE CASCADE,
                    is_active boolean NOT NULL, expires_at timestamptz, mcp_allowed_tools jsonb NOT NULL DEFAULT '[]',
                    mcp_max_tool_calls int NOT NULL DEFAULT 0);
INSERT INTO gwkey SELECT md5(g::text)::uuid, md5(g::text) || md5((g+1)::text), g, true, NULL FROM generate_series(1, {ORGS}) g;
ANALYZE;
"""

TRIGGERS = """
CREATE TABLE amf_model_source_rev (
  org_id bigint PRIMARY KEY REFERENCES org(id) ON DELETE CASCADE,
  rev bigint NOT NULL DEFAULT 1, changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO amf_model_source_rev (org_id) SELECT id FROM org;
CREATE FUNCTION amf_model_bump_orgs(orgs bigint[]) RETURNS void LANGUAGE sql AS $$
  INSERT INTO amf_model_source_rev AS r (org_id)
  SELECT DISTINCT u.o FROM unnest(orgs) AS u(o) JOIN org ON org.id = u.o ORDER BY 1
  ON CONFLICT (org_id) DO UPDATE SET rev = r.rev + 1, changed_at = clock_timestamp();
$$;
CREATE FUNCTION amf_llm_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM amf_model_bump_orgs(ARRAY(
    SELECT x FROM (
      SELECT o.organization_id AS x FROM old_rows o JOIN new_rows n ON n.id = o.id
      WHERE (o.organization_id, o.provider, o.model_name, o.model_id, o.api_key_env_var,
             md5(o.encrypted_api_key), o.api_base, o.region, o.is_active, o.data_sensitivity_level,
             o.compliance_tags, o.created_at)
        IS DISTINCT FROM
            (n.organization_id, n.provider, n.model_name, n.model_id, n.api_key_env_var,
             md5(n.encrypted_api_key), n.api_base, n.region, n.is_active, n.data_sensitivity_level,
             n.compliance_tags, n.created_at)
      UNION
      SELECT n.organization_id FROM old_rows o JOIN new_rows n ON n.id = o.id
      WHERE o.organization_id IS DISTINCT FROM n.organization_id) s));
  RETURN NULL;
END $$;
CREATE FUNCTION amf_llm_ins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM amf_model_bump_orgs(ARRAY(SELECT organization_id FROM new_rows)); RETURN NULL; END $$;
CREATE FUNCTION amf_llm_del() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM amf_model_bump_orgs(ARRAY(SELECT organization_id FROM old_rows)); RETURN NULL; END $$;
CREATE TRIGGER llm_rev_upd AFTER UPDATE ON llm REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION amf_llm_upd();
CREATE TRIGGER llm_rev_ins AFTER INSERT ON llm REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION amf_llm_ins();
CREATE TRIGGER llm_rev_del AFTER DELETE ON llm REFERENCING OLD TABLE AS old_rows
  FOR EACH STATEMENT EXECUTE FUNCTION amf_llm_del();
ALTER TABLE llm ENABLE ALWAYS TRIGGER llm_rev_upd;
ALTER TABLE llm ENABLE ALWAYS TRIGGER llm_rev_ins;
ALTER TABLE llm ENABLE ALWAYS TRIGGER llm_rev_del;
"""

BOUNDED = """
SELECT c.org_id, o.slug, m.id, m.provider, m.model_name, m.model_id, m.api_base, m.region, m.is_active,
       octet_length(m.encrypted_api_key) AS key_len,
       CASE WHEN octet_length(m.encrypted_api_key) BETWEEN 1 AND 6000
            THEN m.encrypted_api_key ~ '^gAAAAA[A-Za-z0-9_=-]+$' END AS key_shape_ok,
       m.api_key_env_var <> '' AS has_env, m.data_sensitivity_level, m.compliance_tags,
       CASE WHEN isfinite(m.created_at) THEN extract(epoch FROM m.created_at)::bigint ELSE 0 END AS created
       {poison}
FROM unnest(%(orgs)s::bigint[]) AS c(org_id)
JOIN org o ON o.id = c.org_id
LEFT JOIN LATERAL (SELECT * FROM llm WHERE llm.organization_id = c.org_id LIMIT 129) m ON true
"""
UNBOUNDED = (
    "SELECT o.slug, m.id, m.provider, m.model_name, m.model_id, m.api_base, m.region, m.is_active, "
    "m.encrypted_api_key, m.api_key_env_var, m.data_sensitivity_level, m.compliance_tags, "
    "extract(epoch FROM m.created_at)::bigint FROM llm m JOIN org o ON o.id = m.organization_id "
    "WHERE m.organization_id = ANY(%(orgs)s) ORDER BY o.slug, m.id"
)
POISON = (
    ", CASE WHEN c.org_id = %(err)s THEN 1 / (c.org_id - c.org_id) "
    "WHEN c.org_id = %(slow)s THEN length((SELECT pg_sleep(1.0))::text) ELSE 0 END AS poison"
)


def rr(conn: psycopg.Connection) -> None:
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.execute(f"SET LOCAL statement_timeout = {BOUND_MS}")


def timed(conn: psycopg.Connection, sql: str, params: dict | tuple | None = None) -> str:
    rr(conn)
    t0 = time.perf_counter()
    try:
        rows = conn.execute(sql, params).fetchall()  # type: ignore[arg-type]
    except psycopg.Error as exc:
        conn.execute("ROLLBACK")
        return f"FAILED {type(exc).__name__} after {(time.perf_counter() - t0) * 1000:,.0f} ms"
    ms = (time.perf_counter() - t0) * 1000
    conn.execute("ROLLBACK")
    return f"ok {len(rows):,} rows, {ms:,.0f} ms"


def section_a(conn: psycopg.Connection, rng: random.Random) -> None:
    print("\n## A. Bounded source read (row-budget chunks, token never selected)")
    print("| chunk | unbounded HEAD-style read | bounded rev-2 read |")
    print("|---|---|---|")
    sql = BOUNDED.format(poison="")
    typical = rng.sample(range(1, ORGS + 1), 400)
    dense = rng.sample(range(ORGS + 1, ORGS + DENSE + 1), 31)
    cases = {
        "400 typical orgs (2,000 rows)": typical,
        "31 dense orgs x 64 (1,984 rows)": dense,
        "399 typical + 250,000 inactive rows in one org": typical[:399] + [WIDE],
        "399 typical + one org with 50 x ~1.4 MB tokens": typical[:399] + [FAT],
        "399 typical + an `infinity` created_at": typical[:399] + [POISON_TS],
        "500 dense orgs x 64 (rev-1 chunk shape)": rng.sample(range(ORGS + 1, ORGS + DENSE + 1), 500),
    }
    for label, orgs in cases.items():
        runs_u = [timed(conn, UNBOUNDED, {"orgs": orgs}) for _ in range(2)]
        runs_b = [timed(conn, sql, {"orgs": orgs}) for _ in range(2)]
        print(f"| {label} | {' / '.join(runs_u)} | {' / '.join(runs_b)} |")
    rr(conn)
    rows = conn.execute(sql, {"orgs": [WIDE, FAT, POISON_TS]}).fetchall()
    conn.execute("ROLLBACK")
    per = {}
    for r in rows:
        per.setdefault(r[0], []).append(r)
    print(f"row counts returned: wide={len(per[WIDE])} (129 = over the 128 cap -> org unavailable), "
          f"fat={len(per[FAT])} key_len={per[FAT][0][9]:,} shape_ok={per[FAT][0][10]} (over cap -> not checked), "
          f"infinity created={per[POISON_TS][0][14]}")


def read_bisect(conn: psycopg.Connection, orgs: list[int], stats: dict[str, int]) -> tuple[list[tuple], list[int]]:
    """Read `orgs`; on any statement error, split and retry inside SAVEPOINTs (same snapshot)."""
    sql = BOUNDED.format(poison=POISON)
    stats["statements"] += 1
    try:
        with conn.transaction():  # SAVEPOINT: an error rolls back to here, the snapshot survives
            rows = conn.execute(sql, {"orgs": orgs, "err": ERR, "slow": SLOW}).fetchall()
        return rows, []
    except psycopg.Error:
        if len(orgs) == 1:
            return [], orgs
        mid = len(orgs) // 2
        a_rows, a_bad = read_bisect(conn, orgs[:mid], stats)
        b_rows, b_bad = read_bisect(conn, orgs[mid:], stats)
        return a_rows + b_rows, a_bad + b_bad


def section_b(conn: psycopg.Connection, rng: random.Random) -> None:
    print("\n## B. Bisection to the org inside one REPEATABLE READ snapshot (SAVEPOINTs)")
    for label, poison in (("statement error (1/0)", ERR), ("statement timeout (pg_sleep 1 s > 800 ms)", SLOW)):
        orgs = rng.sample(range(1, ORGS + 1), 399) + [poison]
        rng.shuffle(orgs)
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = {BOUND_MS}")
        snap0 = conn.execute("SELECT txid_current_snapshot()::text").fetchone()[0]  # type: ignore[index]
        stats = {"statements": 0}
        t0 = time.perf_counter()
        rows, bad = read_bisect(conn, orgs, stats)
        ms = (time.perf_counter() - t0) * 1000
        snap1 = conn.execute("SELECT txid_current_snapshot()::text").fetchone()[0]  # type: ignore[index]
        conn.execute("ROLLBACK")
        got = {r[0] for r in rows}
        print(f"  {label}: isolated={bad} other orgs read={len(got)}/399 statements={stats['statements']} "
              f"wall={ms:,.0f} ms same snapshot={snap0 == snap1}")
    print("  singleton re-read of a quarantined org (next rounds): 1 statement, bounded by the 800 ms bound")


def section_c(conn: psycopg.Connection) -> None:
    print("\n## C. Statement-level revision triggers (transition tables, projected-column filter)")
    t0 = time.perf_counter()
    conn.execute(TRIGGERS)  # type: ignore[arg-type]  # many statements, no parameters
    print(f"  created triggers and backfilled {ORGS + DENSE + 5:,} revision rows in {time.perf_counter() - t0:,.1f} s")

    def bumped() -> int:
        return conn.execute("SELECT count(*) FROM amf_model_source_rev WHERE rev > 1").fetchone()[0]  # type: ignore[index]

    dense_where = f"organization_id BETWEEN {ORGS + 1} AND {ORGS + DENSE}"
    t0 = time.perf_counter()
    conn.execute(f"UPDATE llm SET routing_priority = routing_priority + 1, updated_at = now() WHERE {dense_where}")
    t_np = time.perf_counter() - t0
    print(f"  non-projected bulk update (routing_priority + updated_at), {DENSE * 64:,} rows: "
          f"{t_np:,.1f} s, orgs bumped {bumped():,}")
    conn.execute("ALTER TABLE llm DISABLE TRIGGER llm_rev_upd")
    t0 = time.perf_counter()
    conn.execute(f"UPDATE llm SET routing_priority = routing_priority + 1 WHERE {dense_where}")
    print(f"  same update with the trigger disabled: {time.perf_counter() - t0:,.1f} s")
    conn.execute("ALTER TABLE llm ENABLE ALWAYS TRIGGER llm_rev_upd")
    t0 = time.perf_counter()
    conn.execute(f"UPDATE llm SET is_active = NOT is_active WHERE {dense_where}")
    print(f"  projected bulk update (is_active), {DENSE * 64:,} rows: {time.perf_counter() - t0:,.1f} s, "
          f"orgs bumped {bumped():,} (expected {DENSE:,}; one bump per org per statement)")
    revs = conn.execute(f"SELECT min(rev), max(rev) FROM amf_model_source_rev WHERE org_id BETWEEN {ORGS + 1} AND {ORGS + DENSE}").fetchone()
    print(f"  dense orgs' revisions after one statement: min={revs[0]} max={revs[1]}")  # type: ignore[index]
    before = conn.execute("SELECT rev FROM amf_model_source_rev WHERE org_id = 7").fetchone()[0]  # type: ignore[index]
    with conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute("UPDATE llm SET is_active = false WHERE organization_id = 7")
    after = conn.execute("SELECT rev FROM amf_model_source_rev WHERE org_id = 7").fetchone()[0]  # type: ignore[index]
    print(f"  session_replication_role = replica, org 7 deactivated: rev {before} -> {after} (ENABLE ALWAYS fires)")
    rows = conn.execute(
        "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = 'llm'::regclass AND NOT tgisinternal ORDER BY 1",
    ).fetchall()
    print(f"  projector check pg_trigger.tgenabled: {rows} ('A' = always; anything else -> MODEL uncovered)")
    conn.execute("DELETE FROM org WHERE id = 8")
    gone = conn.execute("SELECT count(*) FROM amf_model_source_rev WHERE org_id = 8").fetchone()[0]  # type: ignore[index]
    print(f"  org delete cascading 5 model deletes: revision row remaining = {gone} (FK ON DELETE CASCADE)")
    t0 = time.perf_counter()
    for i in range(300):
        conn.execute("UPDATE llm SET is_active = NOT is_active WHERE id = %s", (1 + i * 5,))
    on = (time.perf_counter() - t0) / 300 * 1000
    print(f"  single-row projected update, autocommit: {on:.2f} ms each (wall, loaded host)")

    # cross-statement residual: two transactions, disjoint rows, opposite org order
    outcome: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(name: str, first: int, second: int) -> None:
        with psycopg.connect(DSN.rsplit("/", 1)[0] + "/" + DB) as c:
            c.execute("SET lock_timeout = '5s'")
            try:
                with c.transaction():
                    c.execute("UPDATE llm SET is_active = NOT is_active WHERE id = (SELECT min(id) FROM llm WHERE organization_id = %s)", (first,))
                    barrier.wait()
                    c.execute("UPDATE llm SET is_active = NOT is_active WHERE id = (SELECT max(id) FROM llm WHERE organization_id = %s)", (second,))
                outcome[name] = "committed"
            except psycopg.Error as exc:
                outcome[name] = type(exc).__name__

    threads = [threading.Thread(target=worker, args=("T1", 21, 22)), threading.Thread(target=worker, args=("T2", 22, 21))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"  two transactions, disjoint rows, orgs in opposite order across statements: {outcome} "
          "(residual: one is aborted and retried; within ONE statement the org-ordered upsert cannot deadlock)")


def section_d(conn: psycopg.Connection, rng: random.Random) -> None:
    print("\n## D. State-table reads at 100k orgs")
    conn.execute("""
      CREATE TABLE amf_state_record (
        kind text NOT NULL, key text NOT NULL, epoch bigint NOT NULL, seq bigint NOT NULL, feed_seq bigint NOT NULL,
        content_hash text NOT NULL, deleted boolean NOT NULL, op text NOT NULL, signature text NOT NULL,
        body text NOT NULL, engaged boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (kind, key))""")
    conn.execute("CREATE INDEX amf_state_record_feed ON amf_state_record (kind, feed_seq)")
    t0 = time.perf_counter()
    conn.execute(f"""
      INSERT INTO amf_state_record
      SELECT 'model', 'org-' || lpad(g::text, 6, '0'), 1, g, g, md5(g::text) || md5(g::text), false, 'put',
             md5(g::text) || md5((g + 1)::text),
             (SELECT string_agg(md5(g::text || '-' || j::text), '') FROM generate_series(1, 80) j), false, now()
      FROM generate_series(1, {ORGS}) g""")
    conn.execute(f"""
      INSERT INTO amf_state_record
      SELECT 'plan', 'org-' || lpad(g::text, 6, '0'), 1, g, g, md5(g::text) || md5((g+2)::text), false, 'put',
             md5(g::text) || md5((g + 3)::text),
             (SELECT string_agg(md5(g::text || '+' || j::text), '') FROM generate_series(1, 56) j), false, now()
      FROM generate_series(1, {ORGS}) g""")
    conn.execute(f"""
      INSERT INTO amf_state_record
      SELECT 'key', md5(g::text) || md5((g+1)::text), 1, g, g, md5(g::text) || md5((g+4)::text), false, 'put',
             md5(g::text) || md5((g + 5)::text),
             '{{"burst":0.0,"key_id":"' || md5(g::text)::uuid || '","org_id":"org-' || lpad(g::text, 6, '0') || '","rate_per_s":0.0}}',
             false, now()
      FROM generate_series(1, {ORGS}) g""")
    conn.execute("ANALYZE amf_state_record")
    size = conn.execute("SELECT pg_size_pretty(pg_total_relation_size('amf_state_record'))").fetchone()[0]  # type: ignore[index]
    print(f"  loaded 3 x 100k state records ({size}) in {time.perf_counter() - t0:,.0f} s; MODEL bodies 2,560 B of low-compressibility hex")
    print("| read | result under the 800 ms bound |")
    print("|---|---|")
    full = "SELECT kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body FROM amf_state_record WHERE kind = %s ORDER BY feed_seq"
    for label, sql, params in (
        ("MODEL whole-kind snapshot (HEAD repair read)", full, ("model",)),
        ("PLAN whole-kind snapshot (HEAD projector.py:220)", full, ("plan",)),
        ("KEY whole-kind snapshot (HEAD projector.py:220)", full, ("key",)),
        ("PLAN slim snapshot (key, content_hash, deleted, engaged)", "SELECT key, content_hash, deleted, engaged FROM amf_state_record WHERE kind = %s", ("plan",)),
        ("KEY slim snapshot (key, content_hash, deleted, engaged)", "SELECT key, content_hash, deleted, engaged FROM amf_state_record WHERE kind = %s", ("key",)),
        ("MODEL index read (key, feed_seq)", "SELECT key, feed_seq FROM amf_state_record WHERE kind = %s", ("model",)),
        ("MODEL incremental STALE read: 1,000 above a position", "SELECT kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body FROM amf_state_record WHERE kind = 'model' AND feed_seq > %s ORDER BY feed_seq LIMIT 1000", (ORGS - 1000,)),
        ("MODEL targeted CONTENTS read: 100 keys", "SELECT key, body FROM amf_state_record WHERE kind = 'model' AND key = ANY(%s)", ([f"org-{rng.randint(1, ORGS):06d}" for _ in range(100)],)),
        ("PLAN source, every body (HEAD source_pg.py:68-71)", "SELECT o.slug, d.body, d.revision FROM plan_doc d JOIN org o ON o.id = d.organization_id ORDER BY o.slug", None),
        ("PLAN source, revisions only", "SELECT d.organization_id, d.revision FROM plan_doc d", None),
        ("KEY source, every row (HEAD source_pg.py:59-66)", "SELECT k.key_hash, k.id::text, o.slug, (k.is_active AND (k.expires_at IS NULL OR k.expires_at > transaction_timestamp())), k.mcp_allowed_tools, k.mcp_max_tool_calls FROM gwkey k LEFT JOIN org o ON o.id = k.organization_id ORDER BY k.key_hash", None),
    ):
        runs = [timed(conn, sql, params) for _ in range(2)]
        print(f"| {label} | {' / '.join(runs)} |")
    # keyset-paged full repair read
    page_sql = ("SELECT kind, key, epoch, seq, feed_seq, content_hash, deleted, op, signature, body "
                "FROM amf_state_record WHERE kind = 'model' AND key > %s ORDER BY key LIMIT %s")
    for page in (500, 1000):
        rr(conn)
        last, pages, worst, total, nbytes = "", 0, 0.0, 0.0, 0
        while True:
            t0 = time.perf_counter()
            rows = conn.execute(page_sql, (last, page)).fetchall()
            ms = (time.perf_counter() - t0) * 1000
            if not rows:
                break
            pages += 1
            worst = max(worst, ms)
            total += ms
            nbytes += sum(len(r[9]) for r in rows)
            last = rows[-1][1]
        conn.execute("ROLLBACK")
        print(f"  keyset-paged MODEL repair read, {page} records/page: {pages} pages, worst page {worst:,.0f} ms, "
              f"total {total / 1000:,.1f} s, {nbytes / 1e6:,.0f} MB of bodies (one snapshot)")


def section_e() -> None:
    print("\n## E. Writer throughput on the real path (StateWriter + PostgresControlDB + ValkeyPublisher)")
    import redis

    from gateway_v2.domain.state import StateKind
    from gateway_v2.runtime.store_keys import StoreKeys
    from state_control.pg import PostgresControlDB
    from state_control.valkey import ValkeyPublisher
    from state_control.writer import StateWriter

    secret = b"f1a2-evidence-not-a-real-key"
    db = PostgresControlDB(DSN.rsplit("/", 1)[0] + "/" + DB)
    # The reads above made their own amf_state_record; the writer needs the full schema.
    with psycopg.connect(DSN.rsplit("/", 1)[0] + "/" + DB, autocommit=True) as c:
        c.execute("DROP TABLE amf_state_record")
    db.init_schema()
    client = redis.Redis.from_url(REDIS_URL)
    writer = StateWriter(db, ValkeyPublisher(client, secret, StoreKeys(NS)), secret, chunk=500)

    def body(n_bytes: int, i: int) -> dict[str, object]:
        return {"schema": 1, "org_id": f"bench-{i}", "pad": base64.b64encode(os.urandom(n_bytes * 3 // 4)).decode()}

    try:
        for label, size, chunks in (("typical 5-model (~2.1 KB)", 2_100, 10), ("64-model (~25.6 KB)", 25_600, 5)):
            times = []
            for c in range(chunks):
                entries = [(f"bench-{size}-{c}-{i}", body(size, i)) for i in range(100)]
                t0 = time.perf_counter()
                outcome = writer.put_many(StateKind.PLAN, entries, require_new=True)
                times.append((time.perf_counter() - t0) * 1000)
                assert outcome.published, outcome
            times.sort()
            print(f"  put_many 100 x {label}: median {times[len(times) // 2]:,.0f} ms, max {times[-1]:,.0f} ms per chunk "
                  f"({times[len(times) // 2] / 100:.1f} ms per record)")
        times = []
        for i in range(200):
            t0 = time.perf_counter()
            writer.put(StateKind.PLAN, f"bench-2100-0-{i % 100}", body(2_100, i), if_changed=True)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        print(f"  put(if_changed) one existing record, typical body: median {times[100]:.1f} ms, p95 {times[190]:.1f} ms")
    finally:
        deleted = 0
        for key in client.scan_iter(match=f"{NS}*", count=1000):
            deleted += client.delete(key)
        print(f"  scratch Valkey keys deleted: {deleted}")


def main() -> int:
    rng = random.Random(20261111)
    print(f"python={sys.version.split()[0]} psycopg={psycopg.__version__}")
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
            print(f"setup {time.perf_counter() - t0:,.0f} s")
            section_a(conn, rng)
            section_b(conn, rng)
            section_c(conn)
            section_d(conn, rng)
        section_e()
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        print("scratch database dropped")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
