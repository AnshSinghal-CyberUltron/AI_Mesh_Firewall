"""Integration-test fixtures for AI_Mesh_Firewall Phase-0 gates.

Shared by `tests/integration/test_phase0_gates.py`.

Triage decisions baked in:
- E: precondition fixture uses ``pytest.fail`` for config errors and
  ``pytest.skip`` for infra unreachable (defense-in-depth).
- B: hybrid wiring — in-process FastAPI app import for HTTP / monkeypatch
  + real Mongo + Redis + Postgres for sink assertions.
- D: raw psycopg via libpq env vars (no Django bootstrap in test process).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the gateway package importable when running from the AI_Mesh_Firewall
# tree (the package lives under ``gateway/ai_mesh_gateway/``).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATEWAY_PARENT = _REPO_ROOT / "gateway"
if str(_GATEWAY_PARENT) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_PARENT))


# ---------------------------------------------------------------------------
# Phase-0 precondition: config + infra reachability (Triage Agent E)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _check_infra():
    """Fail loudly if config is wrong; skip the suite if infra is unreachable.

    Distinguishes:
      * Config error → ``pytest.fail`` (operator must fix env).
      * Infra unreachable → ``pytest.skip`` (test environment insufficient,
        not a code bug).
    """
    if os.environ.get("GATEWAY_MONGO_TELEMETRY_ENABLED", "").lower() != "true":
        pytest.fail(
            "GATEWAY_MONGO_TELEMETRY_ENABLED must be 'true' for Phase-0 oracles; "
            "every Mongo assertion silently passes a false-positive otherwise."
        )

    # Infra probes — skip rather than fail when services are not up.
    try:
        from pymongo import MongoClient  # type: ignore
        MongoClient(
            os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
            serverSelectionTimeoutMS=2000,
        ).admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"mongo unreachable: {exc}")

    try:
        import redis  # type: ignore
        redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=2,
        ).ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"redis unreachable: {exc}")

    try:
        import psycopg  # type: ignore
        with psycopg.connect(_pg_dsn(), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres unreachable: {exc}")


def _pg_dsn() -> str:
    """Build a libpq DSN from PG* env vars with sane defaults."""
    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'ai_mesh_firewall')} "
        f"user={os.environ.get('PGUSER', 'ai_mesh_firewall')} "
        f"password={os.environ.get('PGPASSWORD', 'ai_mesh_firewall')}"
    )


# ---------------------------------------------------------------------------
# Service clients (sync — avoids event-loop scope tangling)
# ---------------------------------------------------------------------------

@pytest.fixture()
def mongo_db():
    from pymongo import MongoClient  # type: ignore

    client = MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=2000,
    )
    try:
        yield client["aiguardx_telemetry"]
    finally:
        client.close()


@pytest.fixture()
def redis_client():
    import redis  # type: ignore

    client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def pg_conn():
    import psycopg  # type: ignore

    with psycopg.connect(_pg_dsn()) as conn:
        yield conn


@pytest.fixture(autouse=True)
def _reset_motor_client():
    """Reset the global motor client between tests.

    motor's ``AsyncIOMotorClient`` binds to the event loop where it is first
    used. pytest-asyncio creates a fresh loop per async test, so a client
    initialised in test A raises ``Event loop is closed`` when test B tries
    to insert through it. Clear the module-level singleton before each test
    so it is rebuilt on the current loop.
    """
    from ai_mesh_gateway import telemetry_mongo as tm

    tm._client = None
    tm._collection = None
    tm._init_attempted = False
    yield
    tm._client = None
    tm._collection = None
    tm._init_attempted = False
