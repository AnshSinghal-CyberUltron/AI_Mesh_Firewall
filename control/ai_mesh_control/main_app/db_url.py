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
    statement_timeout_ms: int | None = None,
    idle_in_tx_ms: int | None = None,
) -> dict[str, Any]:
    """Return a Django DATABASES['default']-shaped dict for a postgres URL.

    When ``disable_server_side_cursors`` is None, enable it if the env flag is
    truthy OR the URL port is 6432 (PgBouncer).

    ``statement_timeout_ms`` / ``idle_in_tx_ms`` are belt-and-suspenders OPTIONS.
    PgBouncer transaction pooling ignores startup ``options``; callers MUST also
    SET LOCAL inside a transaction (see ``main_app.analytics_db``).
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
    option_parts: list[str] = []
    if statement_timeout_ms is not None:
        option_parts.append(f"-c statement_timeout={int(statement_timeout_ms)}")
    if idle_in_tx_ms is not None:
        option_parts.append(
            f"-c idle_in_transaction_session_timeout={int(idle_in_tx_ms)}"
        )
    if option_parts:
        cfg.setdefault("OPTIONS", {})["options"] = " ".join(option_parts)
    return cfg
