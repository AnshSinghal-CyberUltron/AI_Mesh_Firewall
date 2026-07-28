"""Build Django DATABASES entries from a Postgres URL + pooler-aware flags."""

from __future__ import annotations

from typing import Any
from urllib.parse import ParseResult, urlparse


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def postgres_database_from_url(
    database_url: str,
    *,
    conn_max_age: int = 60,
    disable_server_side_cursors: bool | None = None,
    disable_server_side_cursors_env: str | None = None,
) -> dict[str, Any]:
    """Return a Django DATABASES['default']-shaped dict for a postgres URL.

    When ``disable_server_side_cursors`` is None, enable it if the env flag is
    truthy OR the URL port is 6432 (PgBouncer).
    """
    parsed: ParseResult = urlparse(database_url.strip())
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"Unsupported scheme '{parsed.scheme}'")

    cfg: dict[str, Any] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (parsed.path or "/").lstrip("/") or "postgres",
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": int(conn_max_age),
        "CONN_HEALTH_CHECKS": True,
    }
    if disable_server_side_cursors is None:
        disable_server_side_cursors = _truthy(disable_server_side_cursors_env) or str(
            parsed.port or ""
        ) == "6432"
    if disable_server_side_cursors:
        cfg["DISABLE_SERVER_SIDE_CURSORS"] = True
    return cfg
