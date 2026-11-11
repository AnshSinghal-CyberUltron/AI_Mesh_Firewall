"""GW36 S2 against a real Postgres 16 (skipped without `AMF_PG_DSN`).

What the twin cannot prove: the source SQL over Django-shaped tables, expiry judged by
`transaction_timestamp()`, the fence through a real row lock and `clock_timestamp()`, the
heartbeat read by the database clock, and `init_schema` racing itself.

    AMF_PG_DSN=postgresql://postgres:pg@127.0.0.1:55432/postgres pytest tests/state_control/...
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Iterator

import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.pg import PostgresControlDB
from state_control.projector import Projector
from state_control.publisher import MemoryStore
from state_control.source_pg import PostgresControlSource
from state_control.writer import Fence, LeaseLost, StateWriter
from tests.plan.test_lgw05c_delta import _draft

DSN = os.environ.get("AMF_PG_DSN", "")

pytestmark = pytest.mark.skipif(not DSN, reason="AMF_PG_DSN is not set")

SECRET = b"gw36-s2-pg-secret"

# The columns the projector reads, with Django's types for them (core/models.py, auth/models.py).
DJANGO_TABLES = """
CREATE TABLE auth_api_organization (
  id bigserial PRIMARY KEY, slug varchar(64) UNIQUE NOT NULL, is_active boolean NOT NULL,
  name varchar(255) NOT NULL DEFAULT ''
);
CREATE TABLE core_gatewayapikey (
  id uuid PRIMARY KEY, key_hash varchar(64) UNIQUE NOT NULL,
  organization_id bigint REFERENCES auth_api_organization(id) ON DELETE CASCADE,
  is_active boolean NOT NULL, expires_at timestamptz, key_purpose varchar(32)
);
CREATE TABLE core_v2_plan_document (
  id bigserial PRIMARY KEY,
  organization_id bigint UNIQUE NOT NULL REFERENCES auth_api_organization(id) ON DELETE CASCADE,
  body text NOT NULL, revision bigint NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE core_killswitch (
  id bigserial PRIMARY KEY,
  organization_id bigint REFERENCES auth_api_organization(id) ON DELETE CASCADE,
  model_name varchar(255) NOT NULL, api_key_prefix varchar(64) NOT NULL DEFAULT '',
  is_active boolean NOT NULL, action varchar(16) NOT NULL
);
"""


@pytest.fixture
def scoped() -> Iterator[str]:
    import psycopg

    schema = f"amf_gw36_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
        cursor.execute(f'SET search_path = "{schema}"')
        cursor.execute(DJANGO_TABLES)
    try:
        yield f"{DSN}?options=-csearch_path%3D{schema}"
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin, admin.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _sql(dsn: str, statement: str, params: tuple[object, ...] = ()) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute(statement, params)


def _doc(slug: str) -> str:
    return json.dumps(encode_plan_body(PlanDocument(slug, StreamingMode.INCREMENTAL, (_draft(),))))


def test_two_processes_creating_the_schema_at_once_both_succeed(scoped: str) -> None:
    errors: list[BaseException] = []

    def create() -> None:
        try:
            PostgresControlDB(scoped).init_schema()
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    PostgresControlDB(scoped).init_schema()  # present: no DDL, no lock


def _seed(dsn: str) -> None:
    _sql(dsn, "INSERT INTO auth_api_organization (slug, is_active) VALUES ('acme', true), "
         "('gone', false)")
    _sql(dsn, "INSERT INTO core_gatewayapikey (id, key_hash, organization_id, is_active, "
         "expires_at) VALUES "
         "(gen_random_uuid(), repeat('a', 64), (SELECT id FROM auth_api_organization WHERE slug='acme'), true, NULL), "  # noqa: E501
         "(gen_random_uuid(), repeat('b', 64), (SELECT id FROM auth_api_organization WHERE slug='acme'), true, now() - interval '1 second'), "  # noqa: E501
         "(gen_random_uuid(), repeat('c', 64), NULL, true, NULL), "
         "(gen_random_uuid(), repeat('d', 64), (SELECT id FROM auth_api_organization WHERE slug='gone'), true, NULL)")  # noqa: E501
    _sql(dsn, "INSERT INTO core_v2_plan_document (organization_id, body, revision) "
         "SELECT id, %s, 1 FROM auth_api_organization WHERE slug = 'acme'", (_doc("acme"),))
    _sql(dsn, "INSERT INTO core_killswitch (organization_id, model_name, is_active, action) "
         "SELECT id, '__global__', true, 'disable' FROM auth_api_organization WHERE slug='acme'")


def test_the_source_reads_one_snapshot_and_judges_expiry_by_it(scoped: str) -> None:
    db = PostgresControlDB(scoped)
    db.init_schema()
    _seed(scoped)

    view = PostgresControlSource(db).read()

    keys = {key.key_hash[0]: key for key in view.keys}
    assert keys["a"].active and keys["a"].org_slug == "acme"
    assert not keys["b"].active, "expired at the snapshot's own timestamp"
    assert keys["c"].org_slug is None
    assert [(o.slug, o.active) for o in view.orgs] == [("acme", True), ("gone", False)]
    assert [p.org_slug for p in view.plans] == ["acme"]
    assert [(s.org_slug, s.model_name) for s in view.switches] == [("acme", "__global__")]


def test_a_full_round_over_real_postgres(scoped: str) -> None:
    db = PostgresControlDB(scoped)
    db.init_schema()
    _seed(scoped)
    store = MemoryStore()
    projector = Projector(
        db, PostgresControlSource(db), StateWriter(db, store, SECRET, actor="amf.projector:a"),
        holder="a", knobs=ProjectorKnobs(),
    )

    summary = projector.round_once()

    assert summary.heartbeat and summary.keys_without_org == 1
    live = {r.key for r in db.snapshot(StateKind.KEY)[1] if not r.deleted}
    assert live == {"a" * 64}
    assert [r.key for r in db.snapshot(StateKind.PLAN)[1]] == ["acme"]
    assert db.snapshot(StateKind.KS)[2] == ("org:acme",)
    lag = db.source_lag_s()
    assert lag is not None and lag < 2.0
    assert projector.round_once().writes == 0


def test_the_fence_is_a_real_lease_on_the_database_clock(scoped: str) -> None:
    db = PostgresControlDB(scoped)
    db.init_schema()
    writer = StateWriter(db, MemoryStore(), SECRET)
    body = {"key_id": "k", "org_id": "acme", "rate_per_s": 0.0, "burst": 0.0}

    assert db.claim_lease("a", 5_000)
    assert not db.claim_lease("b", 5_000)
    writer.put(StateKind.KEY, "a" * 64, body, fence=Fence("a", 5_000))
    _sql(scoped, "UPDATE amf_state_projector SET lease_until = clock_timestamp() - interval '1s'")
    assert db.claim_lease("b", 5_000), "an expired lease is taken over"

    before = db.counters(StateKind.KEY)
    with pytest.raises(LeaseLost):
        writer.put(StateKind.KEY, "e" * 64, body, fence=Fence("a", 5_000))
    assert db.counters(StateKind.KEY) == before, "a fenced-out write commits nothing"
    assert not db.mark_projected("a", 0.0)

    db.release_lease("b")
    assert db.claim_lease("a", 5_000), "a released lease is free at once"
