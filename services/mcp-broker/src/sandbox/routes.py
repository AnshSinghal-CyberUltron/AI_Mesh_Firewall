"""Sandbox Controller REST API — ensure, stdio RPC proxy, status, destroy."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_broker_key
from sandbox.docker_health import cached_docker_ok
from sandbox.docker_manager import DockerManager, SandboxContainerInfo

_AGENT_TIMEOUT = float(os.environ.get("MCP_BROKER_AGENT_TIMEOUT", "130"))
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


class StdioRpcRequest(BaseModel):
    server_slug: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    method: str
    params: dict[str, list | dict | None] | list | dict | None = None
    jsonrpc_id: int | str = 1
    timeouts: dict[str, float] = Field(default_factory=dict)


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


async def _post_agent_rpc(
    docker_manager: DockerManager,
    org_slug: str,
    agent_url: str,
    payload: dict[str, Any],
    timeout: float,
) -> httpx.Response:
    """POST one RPC to the sandbox agent, tolerating the cold-start boot window.

    On a connection error (agent socket not yet bound after a fresh/idle
    container start) we re-ensure the container — restarting it if it was reaped
    — refresh the agent URL, back off, and retry, up to a bounded number of
    attempts. Only transport errors are retried; a real HTTP response (any
    status) is returned to the caller unchanged.
    """
    url = agent_url
    last_exc: httpx.HTTPError | None = None
    for attempt in range(1, _AGENT_READY_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(url, json=payload)
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

    @router.post("/{org_slug}/stdio/rpc")
    async def stdio_rpc(org_slug: str, body: StdioRpcRequest) -> dict[str, Any]:
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

        try:
            response = await _post_agent_rpc(
                docker_manager, org_slug, agent_url, payload, timeout
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Sandbox agent unreachable: {exc}",
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

    @router.get("/{org_slug}/status")
    async def sandbox_status(org_slug: str) -> dict[str, Any]:
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
        docker_manager.destroy(org_slug)
        return {"destroyed": True}

    return router
