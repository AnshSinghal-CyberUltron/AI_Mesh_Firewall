"""MCP sandbox-agent — in-container stdio MCP process manager.

Listens on port 9320 inside per-org sandbox containers. The mcp-broker Sandbox
Controller forwards JSON-RPC exchanges via POST /rpc.

Phase 1: health + RPC stub; full stdio spawn logic lands in S2-sandbox-agent.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="MCP Sandbox Agent", version="0.1.0")

ORG_SLUG = os.environ.get("ORG_SLUG", "default")


class RpcRequest(BaseModel):
    server_slug: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    method: str
    params: dict | list | None = None
    jsonrpc_id: int | str = 1
    timeouts: dict[str, float] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "mcp-sandbox-agent",
        "org_slug": ORG_SLUG,
    }


@app.post("/rpc")
async def rpc(body: RpcRequest) -> dict:
    """Proxy one JSON-RPC exchange to a stdio MCP server (S2 implements spawn)."""
    return {
        "jsonrpc": "2.0",
        "id": body.jsonrpc_id,
        "error": {
            "code": -32000,
            "message": (
                f"Sandbox agent RPC not yet wired for server '{body.server_slug}' "
                f"(method={body.method}). See story S2-sandbox-agent."
            ),
        },
    }
