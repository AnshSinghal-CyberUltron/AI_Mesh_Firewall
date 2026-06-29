"""MCP sandbox-agent — in-container stdio MCP process manager.

Listens on port 9320 inside per-org sandbox containers. The mcp-broker Sandbox
Controller forwards JSON-RPC exchanges via POST /rpc.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent.stdio_manager import list_processes, send_jsonrpc, shutdown_all, start_reaper

LOG = logging.getLogger("sandbox_agent")
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_reaper()
    yield
    await shutdown_all()


app = FastAPI(title="MCP Sandbox Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    processes = await list_processes()
    return {
        "status": "ok",
        "service": "mcp-sandbox-agent",
        "org_slug": ORG_SLUG,
        "process_count": len(processes),
    }


@app.post("/rpc")
async def rpc(body: RpcRequest) -> dict:
    """Proxy one JSON-RPC exchange to a stdio MCP server child process."""
    init_timeout = body.timeouts.get("init_seconds")
    method_timeout = body.timeouts.get("method_seconds")
    try:
        return await send_jsonrpc(
            server_slug=body.server_slug,
            command=body.command,
            args=body.args,
            env=body.env,
            method=body.method,
            params=body.params,
            msg_id=body.jsonrpc_id,
            init_timeout=init_timeout,
            method_timeout=method_timeout,
        )
    except RuntimeError as exc:
        LOG.warning("RPC failed for %s/%s method=%s: %s", ORG_SLUG, body.server_slug, body.method, exc)
        return {
            "jsonrpc": "2.0",
            "id": body.jsonrpc_id,
            "error": {"code": -32000, "message": str(exc)},
        }
