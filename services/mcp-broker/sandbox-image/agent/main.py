"""MCP sandbox-agent — transport-agnostic MCP proxy inside per-org sandboxes.

Listens on port 9320. The mcp-broker forwards JSON-RPC via POST /rpc per
SANDBOX_TRANSPORT_CONTRACT.md.
"""

from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Header
from pydantic import BaseModel, Field, model_validator

from agent.stdio_manager import list_processes, send_jsonrpc, shutdown_all, start_reaper
from agent.upstream_manager import (
    UpstreamError,
    connection_counts,
    send_upstream_jsonrpc,
    shutdown_all as shutdown_upstream_all,
)

LOG = logging.getLogger("sandbox_agent")
ORG_SLUG = os.environ.get("ORG_SLUG", "default")
# CHG-0136: OPT-IN broker→agent authentication. When set (provisioned into the
# sandbox env by the broker's docker_manager, matching the broker's own key), the
# /rpc handler requires a matching X-Sandbox-Agent-Key header — a SECOND layer of
# cross-tenant isolation independent of Docker network isolation. Even if a sibling
# sandbox reached this agent's port (e.g. a shared-bridge/host-run topology or a
# network misconfig), it cannot call /rpc without the key — and the clean
# child-env (_build_child_env allowlist + denylist) keeps the key out of every
# spawned MCP server, so a malicious server can't read it. Unset ⇒ allow
# (backward-compatible; the network isolation remains the primary control).
_AGENT_INTERNAL_KEY = os.environ.get("MCP_AGENT_INTERNAL_KEY", "").strip()

TransportKind = Literal["stdio", "streamable-http", "sse", "websocket"]


class StdioConfig(BaseModel):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class UpstreamConfig(BaseModel):
    url: str
    allowed_hosts: list[str]
    headers: dict[str, str] = Field(default_factory=dict)
    oauth_client_role: str = "forbidden_in_sandbox"


class SandboxRpcRequest(BaseModel):
    server_slug: str
    transport: TransportKind = "stdio"
    method: str
    params: dict | list | None = None
    jsonrpc_id: int | str = 1
    timeouts: dict[str, float] = Field(default_factory=dict)
    stdio: StdioConfig | None = None
    upstream: UpstreamConfig | None = None
    # Legacy flat stdio fields (backward compatible with pre-contract broker payloads).
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _merge_legacy_stdio(self) -> SandboxRpcRequest:
        if self.stdio is None and (self.command or self.args or self.env):
            self.stdio = StdioConfig(
                command=self.command or "",
                args=list(self.args),
                env=dict(self.env),
            )
        return self


def _jsonrpc_error(msg_id: int | str, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_reaper()
    yield
    await shutdown_all()
    await shutdown_upstream_all()


app = FastAPI(title="MCP Sandbox Agent", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    processes = await list_processes()
    counts = connection_counts()
    return {
        "status": "ok",
        "service": "mcp-sandbox-agent",
        "org_slug": ORG_SLUG,
        "process_count": len(processes),
        "connection_count": counts,
    }


@app.post("/rpc")
async def rpc(
    body: SandboxRpcRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    x_sandbox_agent_key: str | None = Header(default=None, alias="X-Sandbox-Agent-Key"),
) -> dict[str, Any]:
    """Transport-agnostic JSON-RPC forward (stdio, streamable-http, sse, websocket)."""
    # CHG-0136: opt-in broker→agent auth (defense-in-depth for cross-tenant isolation).
    # Enforced only when MCP_AGENT_INTERNAL_KEY is configured; constant-time compare.
    if _AGENT_INTERNAL_KEY and not hmac.compare_digest(
        x_sandbox_agent_key or "", _AGENT_INTERNAL_KEY
    ):
        LOG.warning("agent rpc rejected: missing/invalid X-Sandbox-Agent-Key org=%s", ORG_SLUG)
        return _jsonrpc_error(body.jsonrpc_id, -32001, "unauthorized: invalid sandbox agent key")
    # CHG-0121: log the propagated correlation id (last trace hop) with SAFE metadata
    # ONLY — never params/args/env/upstream, which can carry PII/secrets (mirrors the
    # broker CHG-0052 log). Makes the trace continuous gateway → broker → sandbox agent.
    LOG.info(
        "agent rpc org=%s server=%s transport=%s method=%s jsonrpc_id=%s request_id=%s",
        ORG_SLUG, body.server_slug, body.transport, body.method, body.jsonrpc_id,
        x_request_id or "-",
    )
    if not body.server_slug.strip():
        return _jsonrpc_error(body.jsonrpc_id, -32602, "server_slug is required")

    transport = body.transport
    init_timeout = body.timeouts.get("init_seconds")
    method_timeout = body.timeouts.get("method_seconds")

    try:
        if transport == "stdio":
            stdio = body.stdio
            if stdio is None or not stdio.command:
                return _jsonrpc_error(body.jsonrpc_id, -32602, "stdio.command is required")
            return await send_jsonrpc(
                server_slug=body.server_slug,
                command=stdio.command,
                args=stdio.args,
                env=stdio.env,
                method=body.method,
                params=body.params,
                msg_id=body.jsonrpc_id,
                init_timeout=init_timeout,
                method_timeout=method_timeout,
            )

        if transport in ("streamable-http", "sse", "websocket"):
            if body.upstream is None:
                return _jsonrpc_error(
                    body.jsonrpc_id, -32602, "upstream block is required for remote transports"
                )
            return await send_upstream_jsonrpc(
                server_slug=body.server_slug,
                transport=transport,
                upstream=body.upstream.model_dump(),
                method=body.method,
                params=body.params,
                msg_id=body.jsonrpc_id,
                timeouts=body.timeouts,
            )

        return _jsonrpc_error(body.jsonrpc_id, -32004, f"unsupported transport: {transport}")

    except UpstreamError as exc:
        return _jsonrpc_error(body.jsonrpc_id, exc.code, exc.message)
    except RuntimeError as exc:
        LOG.warning(
            "RPC failed for %s/%s method=%s: %s",
            ORG_SLUG,
            body.server_slug,
            body.method,
            exc,
        )
        return _jsonrpc_error(body.jsonrpc_id, -32000, str(exc))
    except Exception:  # noqa: BLE001 — final safety net
        # CHG-0135: an UNEXPECTED exception from a transport handler (TimeoutError,
        # OSError, ValueError, a bug — all more likely under chaos: connection resets,
        # partial failures) must still return a STRUCTURED JSON-RPC error carrying the
        # request id, NOT escape to a raw HTTP 500 (which loses the jsonrpc_id correlation
        # + the error classification the broker/gateway rely on). GENERIC message only —
        # the full detail is logged server-side via LOG.exception (SAFE metadata: org /
        # server / method, never params/args), never returned, so an unknown exception's
        # text cannot leak internal detail to the caller. asyncio.CancelledError is a
        # BaseException (not Exception), so cancellation still propagates uncaught.
        LOG.exception(
            "RPC unexpected error for %s/%s method=%s",
            ORG_SLUG,
            body.server_slug,
            body.method,
        )
        return _jsonrpc_error(body.jsonrpc_id, -32000, "internal sandbox agent error")
