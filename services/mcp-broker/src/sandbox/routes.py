"""Sandbox Controller REST API — ensure, stdio RPC proxy, status, destroy."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_broker_key
from sandbox.docker_manager import DockerManager

_AGENT_TIMEOUT = float(os.environ.get("MCP_BROKER_AGENT_TIMEOUT", "130"))


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


def _require_docker(docker_manager: DockerManager) -> None:
    if not docker_manager.ping():
        raise HTTPException(status_code=503, detail="Docker unavailable")


def _check_org_quota(docker_manager: DockerManager, org_slug: str) -> None:
    registry = docker_manager._registry  # noqa: SLF001 — lifecycle-owned registry
    if registry is None:
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


def build_sandbox_router(docker_manager: DockerManager) -> APIRouter:
    router = APIRouter(
        prefix="/v1/sandbox",
        tags=["sandbox"],
        dependencies=[Depends(require_broker_key)],
    )

    @router.post("/{org_slug}/ensure")
    def ensure_sandbox(org_slug: str, body: EnsureRequest) -> dict[str, Any]:
        _require_docker(docker_manager)
        _check_org_quota(docker_manager, org_slug)
        response = _ensure_response(docker_manager, org_slug)
        docker_manager.touch_activity(org_slug)
        return response

    @router.post("/{org_slug}/stdio/rpc")
    async def stdio_rpc(org_slug: str, body: StdioRpcRequest) -> dict[str, Any]:
        _require_docker(docker_manager)
        _check_org_quota(docker_manager, org_slug)
        info = docker_manager.ensure(org_slug)
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
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(agent_url, json=payload)
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
        container = docker_manager.find_container(org_slug)
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
