"""GW36 S2 N23: what one projector round costs on real Postgres at N keys.

    AMF_PG_DSN=postgresql://postgres:pg@127.0.0.1:55432/postgres \
        gateway_v2/.venv/bin/python docs/plans/evidence/2026-11-03-gw36-s2/measure_scale.py 10000 200

Publishes into the in-memory store, so this is the Postgres + diff cost of a round (the part that
is O(records)); a Valkey publish is per changed record and does not depend on N.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../../gateway_v2"))

from gateway_v2.domain.plan import StreamingMode  # noqa: E402
from gateway_v2.domain.state_knobs import ProjectorKnobs  # noqa: E402
from gateway_v2.plan.document import PlanDocument, encode_plan_body  # noqa: E402
from state_control.pg import PostgresControlDB  # noqa: E402
from state_control.projector import Projector  # noqa: E402
from state_control.publisher import MemoryStore  # noqa: E402
from state_control.source_pg import PostgresControlSource  # noqa: E402
from state_control.writer import StateWriter  # noqa: E402
from tests.plan.test_lgw05c_delta import _draft  # noqa: E402
from tests.state_control.test_gw36_s2_pg_live import DJANGO_TABLES  # noqa: E402


def main(keys: int, orgs: int) -> None:
    dsn = os.environ["AMF_PG_DSN"]
    schema = f"amf_gw36_scale_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as admin, admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path = "{schema}"')
        cur.execute(DJANGO_TABLES)
        cur.execute(
            "INSERT INTO auth_api_organization (slug, is_active) "
            "SELECT 'org-' || g, true FROM generate_series(1, %s) g",
            (orgs,),
        )
        cur.execute(
            "INSERT INTO core_gatewayapikey (id, key_hash, organization_id, is_active) "
            "SELECT gen_random_uuid(), encode(sha256(g::text::bytea), 'hex'), 1 + g %% %s, true "
            "FROM generate_series(1, %s) g",
            (orgs, keys),
        )
        for org in range(1, orgs + 1):
            body = json.dumps(
                encode_plan_body(
                    PlanDocument(f"org-{org}", StreamingMode.INCREMENTAL, (_draft(),)),
                ),
            )
            cur.execute(
                "INSERT INTO core_v2_plan_document (organization_id, body, revision) "
                "SELECT id, %s, 1 FROM auth_api_organization WHERE slug = %s",
                (body, f"org-{org}"),
            )
    scoped = f"{dsn}?options=-csearch_path%3D{schema}"
    db = PostgresControlDB(scoped, lock_timeout_ms=250, statement_timeout_ms=800)
    db.init_schema()
    projector = Projector(
        db, PostgresControlSource(db), StateWriter(db, MemoryStore(), b"s"),
        holder="scale", knobs=ProjectorKnobs(),
    )
    rows = []
    try:
        for label in ("onboard",) * 8 + ("steady", "steady"):
            started = time.perf_counter()
            summary = projector.round_once()
            took = time.perf_counter() - started
            rows.append((label, round(took * 1000, 1), summary.writes, summary.adds_deferred))
            if label == "onboard" and summary.adds_deferred == 0 and summary.adds == 0:
                break
        with psycopg.connect(scoped, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("UPDATE core_gatewayapikey SET is_active = false WHERE ctid IN "
                        "(SELECT ctid FROM core_gatewayapikey LIMIT 1)")
        started = time.perf_counter()
        summary = projector.round_once()
        rows.append(("one revoke", round((time.perf_counter() - started) * 1000, 1),
                     summary.writes, summary.adds_deferred))
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin, admin.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
    print(f"keys={keys} orgs={orgs}")
    print("round        ms      writes  adds_deferred")
    for label, ms, writes, deferred in rows:
        print(f"{label:<12} {ms:>8} {writes:>8} {deferred:>8}")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
