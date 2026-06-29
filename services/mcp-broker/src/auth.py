"""Internal broker authentication — fail closed on missing/invalid key."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

BROKER_KEY_HEADER = "X-MCP-Broker-Key"


def expected_broker_key() -> str | None:
    value = os.environ.get("MCP_BROKER_INTERNAL_KEY", "").strip()
    return value or None


def require_broker_key(
    x_mcp_broker_key: str | None = Header(None, alias=BROKER_KEY_HEADER),
) -> None:
    """Reject requests without a valid internal broker key."""
    expected = expected_broker_key()
    if not expected or not x_mcp_broker_key or x_mcp_broker_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing broker key")
