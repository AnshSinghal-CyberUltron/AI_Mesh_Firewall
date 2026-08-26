"""Phase 0a C-7: analytics DB alias + transaction-scoped timeouts.

PgBouncer in transaction pooling ignores startup `options`
(`IGNORE_STARTUP_PARAMETERS` includes `options`). A connection-level
`statement_timeout` set via Django OPTIONS therefore never reaches Postgres.
Timeouts MUST be applied with SET LOCAL inside a transaction on this alias.
"""

from __future__ import annotations

ANALYTICS_DB_ALIAS = "analytics"
STATEMENT_TIMEOUT_MS = 5000
IDLE_IN_TX_MS = 30000


def analytics_set_local_timeout_sql() -> list[str]:
    return [
        f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}",
        f"SET LOCAL idle_in_transaction_session_timeout = {IDLE_IN_TX_MS}",
    ]


# Paths whose queries run on the analytics alias and must inherit SET LOCAL.
_ANALYTICS_PATH_PREFIXES = ("/api/security/", "/api/dashboard/")


def is_analytics_http_path(path: str) -> bool:
    p = path or ""
    return any(p.startswith(prefix) for prefix in _ANALYTICS_PATH_PREFIXES)


_TIMEOUT_MARKERS = ("statement timeout", "canceling statement")


def is_statement_timeout(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _TIMEOUT_MARKERS)


def analytics_timeout_payload() -> dict:
    return {
        "error": "query_timeout",
        "detail": "Analytics query exceeded statement_timeout",
        "code": "query_timeout",
    }


def apply_analytics_timeout() -> None:
    """SET LOCAL timeouts on the analytics connection. No-op on sqlite."""
    from django.db import connections

    if ANALYTICS_DB_ALIAS not in connections.databases:
        return
    conn = connections[ANALYTICS_DB_ALIAS]
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cursor:
        for sql in analytics_set_local_timeout_sql():
            cursor.execute(sql)
