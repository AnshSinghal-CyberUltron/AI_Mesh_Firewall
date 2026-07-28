"""Unit tests for postgres_database_from_url (PgBouncer-aware Django DB config)."""

from __future__ import annotations

import pytest

from main_app.db_url import postgres_database_from_url


def test_pgbouncer_port_disables_server_side_cursors():
    db = postgres_database_from_url(
        "postgresql://u:p@pgbouncer:6432/ai_mesh_firewall",
        conn_max_age=0,
    )
    assert db["HOST"] == "pgbouncer"
    assert db["PORT"] == "6432"
    assert db["CONN_MAX_AGE"] == 0
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True


def test_disable_server_side_cursors_env_flag():
    db = postgres_database_from_url(
        "postgresql://u:p@postgres:5432/ai_mesh_firewall",
        conn_max_age=0,
        disable_server_side_cursors_env="true",
    )
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True


def test_direct_postgres_keeps_cursors_unless_flagged():
    db = postgres_database_from_url(
        "postgresql://u:p@postgres:5432/ai_mesh_firewall",
        conn_max_age=30,
    )
    assert db["HOST"] == "postgres"
    assert db["PORT"] == "5432"
    assert db["CONN_MAX_AGE"] == 30
    assert "DISABLE_SERVER_SIDE_CURSORS" not in db


def test_unsupported_scheme_raises():
    with pytest.raises(ValueError, match="Unsupported scheme"):
        postgres_database_from_url("mysql://u:p@h/db")
