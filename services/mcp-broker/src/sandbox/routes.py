"""Sandbox Controller REST API — ensure, stdio RPC proxy, status, destroy."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from auth import require_broker_key
from sandbox.docker_health import cached_docker_ok
from sandbox.docker_manager import DockerManager, SandboxContainerInfo

LOG = logging.getLogger("mcp_broker.sandbox_rpc")

# CHG-0111: the broker key (X-MCP-Broker-Key) is a SHARED secret, not per-org — so the
# path ``org_slug`` is the SOLE tenant selector for sandbox routing. ``DockerManager``
# LOSSILY sanitizes it for the container/volume/network name
# (``re.sub(r"[^a-zA-Z0-9_.-]", "-", slug).strip("-")``), so two DISTINCT slugs can COLLIDE
# onto one container ("acme/prod" == "acme-prod"; "-acme" == "acme-" == "acme"; any
# all-invalid/empty slug -> "default"), and ``find_container`` matches by that name FIRST —
# a CROSS-TENANT hazard (org B's tool call would execute in / destroy org A's sandbox).
# Fail CLOSED at the boundary: accept ONLY a slug that the sanitizer maps 1:1 (no
# substitution, no strip, non-empty, bounded) so distinct tenants can never share a sandbox.
_MAX_ORG_SLUG_LEN = 64


def _require_canonical_org_slug(org_slug: str) -> None:
    """Reject (400) any ``org_slug`` that ``DockerManager`` would lossily sanitize or
    strip. Such a slug is not a 1:1 tenant key and could collide onto another org's
    sandbox — so it must never be resolved to a container. Only exactly-canonical slugs
    (``[a-zA-Z0-9_.-]``, no leading/trailing ``-``, 1..64 chars) are routed."""
    if (
        not org_slug
        or len(org_slug) > _MAX_ORG_SLUG_LEN
        or re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") != org_slug
    ):
        raise HTTPException(status_code=400, detail="Invalid org_slug")


_AGENT_TIMEOUT = float(os.environ.get("MCP_BROKER_AGENT_TIMEOUT", "130"))
# CHG-0146: hard CEILING on the effective per-RPC agent timeout. The caller-supplied
# ``body.timeouts`` (init_seconds/method_seconds) is folded in via ``max(...)`` with no
# upper bound, so an oversized value (a misconfigured/malicious/non-gateway caller) would
# hold a broker->agent httpx connection + the serving worker open for that whole duration
# — under concurrency a connection-pool / event-loop exhaustion DoS. The broker
# self-defends (like the gateway's inbound body cap) rather than trusting the caller to
# send sane values. Kept >= the base timeout so an operator's explicit MCP_BROKER_AGENT_
# TIMEOUT is never clipped; generous (15 min) so no legitimate slow cold-start/tool call
# is affected.
_AGENT_TIMEOUT_MAX = max(
    _AGENT_TIMEOUT,
    float(os.environ.get("MCP_BROKER_AGENT_TIMEOUT_MAX", "900")),
)
# CHG-0151: hard CEILING on the bytes the broker will buffer from a sandbox agent's RPC
# response. The broker reads the agent reply (``response.json()``/``.text``) with no size
# bound, so a buggy — or COMPROMISED (the sandbox runs untrusted tenant code; gVisor +
# per-org auth are the containment, but the broker must not TRUST the agent) — per-org agent
# returning a huge body would OOM the SHARED broker: a cross-tenant availability breach
# (every org's sandbox routes through this one broker). 16 MiB = 2× the agent's own upstream
# self-cap (MCP_AGENT_MAX_RESPONSE_BYTES, 8 MiB) so a legitimate max-size reply is never
# clipped; env-tunable.
_AGENT_MAX_RESPONSE_BYTES = int(
    os.environ.get("MCP_BROKER_AGENT_MAX_RESPONSE_BYTES", str(16 * 1024 * 1024))
)
# Cold-start agent-readiness window. A freshly (re)started sandbox container
# reports "running" as soon as its PID-1 process starts, but the in-container
# HTTP agent takes a beat to bind its socket. Without tolerating that window the
# very first RPC after a cold start raises a connection error -> broker 502 ->
# gateway surfaces "MCP sandbox is temporarily unavailable" (bug #4). Retry the
# agent POST with bounded backoff, re-ensuring between attempts.
_AGENT_READY_RETRIES = int(os.environ.get("MCP_SANDBOX_AGENT_READY_RETRIES", "8"))
_AGENT_READY_BASE_DELAY = float(os.environ.get("MCP_SANDBOX_AGENT_READY_BASE_DELAY", "0.4"))
_AGENT_READY_MAX_DELAY = float(os.environ.get("MCP_SANDBOX_AGENT_READY_MAX_DELAY", "2.0"))


class EnsureRequest(BaseModel):
    org_id: str | None = None
    warm: bool = True


class StdioConfig(BaseModel):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class UpstreamConfig(BaseModel):
    url: str
    allowed_hosts: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    oauth_client_role: str = "forbidden_in_sandbox"


class SandboxRpcRequest(BaseModel):
    """Transport-agnostic RPC envelope forwarded verbatim to the sandbox agent
    (matches ``sandbox-image/agent/main.py`` ``SandboxRpcRequest``).

    P4.13/P6.18: all four transports (stdio, streamable-http, sse, websocket) route
    through the per-org sandbox so the gateway never dials upstream MCP URLs
    directly. ``command``/``args``/``env`` remain as LEGACY flat stdio fields so the
    pre-contract gateway payload (and the deprecated ``/stdio/rpc`` route) keep
    working unchanged; the agent's own legacy-merge builds ``stdio`` from them.
    """

    server_slug: str
    transport: str = "stdio"
    method: str
    params: dict[str, list | dict | None] | list | dict | None = None
    jsonrpc_id: int | str = 1
    timeouts: dict[str, float] = Field(default_factory=dict)
    stdio: StdioConfig | None = None
    upstream: UpstreamConfig | None = None
    # Legacy flat stdio fields (backward compatible with pre-contract gateway payloads).
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


# Backward-compatible alias — the deprecated /stdio/rpc route still names this type.
StdioRpcRequest = SandboxRpcRequest


def _iso_timestamp(value: float | datetime | None = None) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc)
    elif value is not None:
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _max_orgs() -> int:
    return int(os.environ.get("MCP_SANDBOX_MAX_ORGS", "50"))


def _require_docker(_docker_manager: DockerManager) -> None:
    if not cached_docker_ok():
        raise HTTPException(status_code=503, detail="Docker unavailable")


def _check_org_quota(docker_manager: DockerManager, org_slug: str) -> None:
    registry = docker_manager._registry  # noqa: SLF001 — lifecycle-owned registry
    if registry is None:
        return
    if registry.get(org_slug) is not None:
        return
    if docker_manager.find_container(org_slug) is not None:
        return
    if len(registry.list()) >= _max_orgs():
        raise HTTPException(status_code=429, detail="Org sandbox quota exceeded")


def _ensure_response(docker_manager: DockerManager, org_slug: str) -> dict[str, Any]:
    info = docker_manager.ensure(org_slug)
    registry = docker_manager._registry  # noqa: SLF001
    entry = registry.get(org_slug) if registry else None
    last_activity = entry.last_activity if entry else time.time()
    return {
        "org_slug": org_slug,
        "container_id": info.container_id,
        "status": info.status,
        "agent_url": info.agent_url,
        "created_at": _iso_timestamp(info.created_at),
        "last_activity": _iso_timestamp(last_activity),
    }


def _warm_ready_timeout() -> float:
    return float(os.environ.get("MCP_SANDBOX_WARM_READY_TIMEOUT", "20"))


def _warm_ready_interval() -> float:
    return float(os.environ.get("MCP_SANDBOX_WARM_READY_INTERVAL", "0.5"))


async def _agent_health_ok(agent_url: str | None) -> bool:
    """True when the in-container agent HTTP socket is bound (GET /health 2xx)."""
    if not agent_url:
        return False
    health_url = f"{agent_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
            return resp.status_code < 400
    except httpx.HTTPError:
        return False


async def _wait_agent_ready(agent_url: str | None, timeout: float) -> bool:
    """Poll the in-container agent /health until ready or the bounded deadline.

    B3 item#19 — when a caller *warms* the sandbox we block (bounded) until the
    agent socket binds, so their first RPC hits a READY agent instead of racing
    the cold start (which surfaced "MCP sandbox is temporarily unavailable").
    ``timeout <= 0`` disables the wait (used by unit tests to avoid real HTTP).
    """
    if timeout <= 0 or not agent_url:
        return False
    deadline = time.time() + timeout
    interval = _warm_ready_interval()
    while True:
        if await _agent_health_ok(agent_url):
            return True
        if time.time() >= deadline:
            return False
        await asyncio.sleep(interval)


async def _resolve_running_sandbox(
    docker_manager: DockerManager, org_slug: str
) -> SandboxContainerInfo:
    """Refresh agent_url from live Docker state; fall back to ensure when missing."""
    container = await asyncio.to_thread(docker_manager.find_container, org_slug)
    if container is not None:
        info = docker_manager.to_info(org_slug, container)
        if info.status == "running" and info.agent_url:
            docker_manager._sync_registry(info)
            return info
    return await asyncio.to_thread(docker_manager.ensure, org_slug)


async def _read_agent_response_capped(
    client: "httpx.AsyncClient",
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None,
) -> httpx.Response:
    """POST to the sandbox agent and STREAM the reply with a hard byte cap (CHG-0151).

    The broker is SHARED across all orgs; the sandbox agent runs untrusted tenant code, so
    the broker must bound what it buffers rather than call ``client.post()`` (which reads the
    whole body into memory unbounded). Reads incrementally, aborts with HTTP 502 the instant
    the running total crosses ``_AGENT_MAX_RESPONSE_BYTES``, then rebuilds a fully-read
    ``httpx.Response`` the caller can ``.json()``/``.status_code``/``.text`` unchanged. A
    transport error while opening the stream (agent not bound yet on a cold start) still
    raises ``httpx.HTTPError`` so ``_post_agent_rpc``'s retry loop sees it exactly as before.
    """
    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        status, hdrs, req = resp.status_code, resp.headers, resp.request
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if len(buf) > _AGENT_MAX_RESPONSE_BYTES:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Sandbox agent response exceeded the "
                        f"{_AGENT_MAX_RESPONSE_BYTES}-byte ceiling"
                    ),
                )
    return httpx.Response(
        status_code=status, headers=hdrs, content=bytes(buf), request=req
    )


async def _post_agent_rpc(
    docker_manager: DockerManager,
    org_slug: str,
    agent_url: str,
    payload: dict[str, Any],
    timeout: float,
    request_id: str | None = None,
) -> httpx.Response:
    """POST one RPC to the sandbox agent, tolerating the cold-start boot window.

    On a connection error (agent socket not yet bound after a fresh/idle
    container start) we re-ensure the container — restarting it if it was reaped
    — refresh the agent URL, back off, and retry, up to a bounded number of
    attempts. Only transport errors are retried; a real HTTP response (any
    status) is returned to the caller unchanged.

    CHG-0121: forward the propagated ``X-Request-ID`` to the in-container agent
    (last trace hop) so the agent can log it and the trace is continuous
    gateway -> broker -> sandbox agent.
    """
    url = agent_url
    # CHG-0121: propagate the trace id. CHG-0136: attach the opt-in broker→agent key
    # (X-Sandbox-Agent-Key) when configured — a second cross-tenant isolation layer the
    # agent verifies (docker_manager provisions the SAME key into the sandbox env). Unset
    # ⇒ no header (backward-compatible; the agent then does not require it).
    _headers: dict[str, str] = {}
    if request_id:
        _headers["X-Request-ID"] = request_id
    _agent_key = os.environ.get("MCP_AGENT_INTERNAL_KEY", "").strip()
    if _agent_key:
        _headers["X-Sandbox-Agent-Key"] = _agent_key
    last_exc: httpx.HTTPError | None = None
    for attempt in range(1, _AGENT_READY_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await _read_agent_response_capped(
                    client, url, payload, _headers or None
                )
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= _AGENT_READY_RETRIES:
                break
            # Re-ensure to (re)start a stopped/crashed container and refresh the
            # agent URL, then wait for the agent to finish binding.
            try:
                info = await asyncio.to_thread(docker_manager.ensure, org_slug)
                if info.status == "running" and info.agent_url:
                    url = f"{info.agent_url.rstrip('/')}/rpc"
            except Exception:
                pass
            delay = min(_AGENT_READY_BASE_DELAY * attempt, _AGENT_READY_MAX_DELAY)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def build_sandbox_router(docker_manager: DockerManager) -> APIRouter:
    router = APIRouter(
        prefix="/v1/sandbox",
        tags=["sandbox"],
        dependencies=[Depends(require_broker_key)],
    )

    @router.post("/{org_slug}/ensure")
    async def ensure_sandbox(org_slug: str, body: EnsureRequest) -> dict[str, Any]:
        _require_canonical_org_slug(org_slug)  # CHG-0111: no cross-tenant slug collision
        _require_docker(docker_manager)
        _check_org_quota(docker_manager, org_slug)
        response = await asyncio.to_thread(_ensure_response, docker_manager, org_slug)
        docker_manager.touch_activity(org_slug)
        # B3 item#19: honor `warm` — wait (bounded) for the in-container agent to
        # bind so the caller's first RPC hits a ready agent (no cold-start 502
        # race). `provisioning` lets the caller distinguish "still starting" from
        # a ready sandbox or a hard failure.
        agent_ready = False
        if body.warm and response.get("status") == "running":
            agent_ready = await _wait_agent_ready(
                response.get("agent_url"), _warm_ready_timeout()
            )
        response["agent_ready"] = agent_ready
        response["provisioning"] = bool(body.warm and not agent_ready)
        return response

    async def _forward_sandbox_rpc(
        org_slug: str, body: SandboxRpcRequest, request_id: str | None = None
    ) -> dict[str, Any]:
        """Resolve the running per-org sandbox and forward one RPC to its agent.

        Transport-agnostic (P4.13/P6.18): the same path serves stdio, streamable-http,
        sse and websocket — the agent picks the transport from ``body.transport`` and
        dials the upstream from inside the org sandbox (egress-allowlisted), so the
        gateway never connects to an external MCP URL directly.
        """
        _require_canonical_org_slug(org_slug)  # CHG-0111: no cross-tenant slug collision
        # CHG-0052: log the forward (safe metadata ONLY — never params/args/env/upstream,
        # which can carry PII/secrets) with the propagated X-Request-ID (CHG-0051), so a
        # tool call is traceable gateway audit -> broker. Logged before the docker/sandbox
        # resolution so failed (503) calls are traced too.
        LOG.info(
            "sandbox rpc org=%s server=%s transport=%s method=%s jsonrpc_id=%s request_id=%s",
            org_slug, body.server_slug, body.transport, body.method,
            body.jsonrpc_id, request_id or "-",
        )
        if not cached_docker_ok():
            raise HTTPException(status_code=503, detail="Docker unavailable")
        info = await _resolve_running_sandbox(docker_manager, org_slug)
        if info.status != "running" or not info.agent_url:
            raise HTTPException(status_code=503, detail="Sandbox not running")

        docker_manager.touch_activity(org_slug)
        agent_url = f"{info.agent_url.rstrip('/')}/rpc"
        payload = body.model_dump()
        timeout = _AGENT_TIMEOUT
        if body.timeouts:
            timeout = max(
                timeout,
                body.timeouts.get("init_seconds", 0),
                body.timeouts.get("method_seconds", 0),
            )
        # CHG-0146: clamp the caller-influenced timeout to a hard ceiling so no single RPC
        # can pin a broker->agent connection open for an unbounded time (DoS containment).
        if timeout > _AGENT_TIMEOUT_MAX:
            LOG.warning(
                "sandbox rpc org=%s: requested agent timeout %.0fs exceeds ceiling %.0fs; clamping",
                org_slug, timeout, _AGENT_TIMEOUT_MAX,
            )
            timeout = _AGENT_TIMEOUT_MAX

        try:
            response = await _post_agent_rpc(
                docker_manager, org_slug, agent_url, payload, timeout,
                request_id=request_id,  # CHG-0121: last trace hop → the sandbox agent
            )
        except httpx.HTTPError as exc:
            # B3 #20: the agent socket is still not bound after the cold-start
            # retries — the sandbox is (most likely) still PROVISIONING (e.g. a
            # slow first-time npx fetch), not hard-broken. Report a RETRYABLE 503
            # provisioning state so the gateway client backs off + retries, rather
            # than the hard 502 the client maps to "MCP sandbox is temporarily
            # unavailable". A bound agent returning 5xx/4xx is still a 502 below.
            raise HTTPException(
                status_code=503,
                detail=f"Sandbox provisioning: agent not ready yet ({exc})",
            ) from exc

        if response.status_code >= 500:
            raise HTTPException(
                status_code=502,
                detail=f"Sandbox agent error: HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=response.text or f"Sandbox agent error: HTTP {response.status_code}",
            )
        return response.json()

    @router.post("/{org_slug}/rpc")
    async def sandbox_rpc(
        org_slug: str,
        body: SandboxRpcRequest,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        """Unified transport-agnostic RPC (P4.13/P6.18) — stdio + remote transports."""
        return await _forward_sandbox_rpc(org_slug, body, request_id=x_request_id)

    @router.post("/{org_slug}/stdio/rpc")
    async def stdio_rpc(
        org_slug: str,
        body: SandboxRpcRequest,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> dict[str, Any]:
        """DEPRECATED alias of ``/{org_slug}/rpc`` — retained for the pre-contract
        gateway payload. Forces ``transport=stdio`` for old callers that omit it."""
        if not body.transport:
            body.transport = "stdio"
        return await _forward_sandbox_rpc(org_slug, body, request_id=x_request_id)

    @router.get("/{org_slug}/status")
    async def sandbox_status(org_slug: str) -> dict[str, Any]:
        _require_canonical_org_slug(org_slug)  # CHG-0111: no cross-tenant slug collision
        container = await asyncio.to_thread(docker_manager.find_container, org_slug)
        info = docker_manager.to_info(org_slug, container)
        processes: list[dict[str, Any]] = []

        if info.status == "running" and info.agent_url:
            health_url = f"{info.agent_url.rstrip('/')}/health"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    health_resp = await client.get(health_url)
                if health_resp.status_code == 200:
                    health = health_resp.json()
                    raw_processes = health.get("processes")
                    if isinstance(raw_processes, list):
                        for proc in raw_processes:
                            if not isinstance(proc, dict):
                                continue
                            processes.append(
                                {
                                    "server_slug": proc.get("server_slug")
                                    or proc.get("key", ""),
                                    "running": bool(proc.get("running")),
                                    "initialized": bool(proc.get("initialized")),
                                    "last_used": proc.get("last_used", 0),
                                }
                            )
            except httpx.HTTPError:
                pass

        return {
            "org_slug": org_slug,
            "status": info.status,
            "container_id": info.container_id,
            "processes": processes,
            "resource": {
                "cpu_limit": str(docker_manager.config.cpus),
                "memory_limit_mb": docker_manager.config.memory_mb,
            },
        }

    @router.delete("/{org_slug}")
    def destroy_sandbox(org_slug: str) -> dict[str, bool]:
        _require_canonical_org_slug(org_slug)  # CHG-0111: never destroy a collided sandbox
        docker_manager.destroy(org_slug)
        return {"destroyed": True}

    return router
