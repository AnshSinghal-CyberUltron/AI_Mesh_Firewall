"""MCP broker scaffold — tool mediation and policy enforcement."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sandbox.docker_health import bind_docker_manager, cached_docker_ok
from sandbox.docker_manager import DockerManager
from sandbox.registry import SandboxRegistry
from sandbox.reaper import start_reaper, stop_reaper
from sandbox.routes import build_sandbox_router

sandbox_registry = SandboxRegistry()
docker_manager = DockerManager(registry=sandbox_registry)
bind_docker_manager(docker_manager)

_DOCKER_OK_TTL_SECONDS = 15.0


async def _docker_ok_refresh_loop() -> None:
    while True:
        await asyncio.sleep(_DOCKER_OK_TTL_SECONDS)
        await asyncio.to_thread(cached_docker_ok, force=True)


async def _warm_docker_ok() -> None:
    """Populate docker_ok cache soon after startup without blocking request handling."""
    for _ in range(12):
        await asyncio.to_thread(cached_docker_ok, force=True)
        if cached_docker_ok():
            return
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    asyncio.create_task(_warm_docker_ok())
    refresh_task = asyncio.create_task(_docker_ok_refresh_loop(), name="docker-ok-refresh")
    start_reaper(sandbox_registry, docker_manager)
    yield
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass
    await stop_reaper()


app = FastAPI(title="AI Mesh MCP Broker", version="0.1.0", lifespan=lifespan)
app.include_router(build_sandbox_router(docker_manager))


@app.get("/health")
def health() -> dict:
    # Never block liveness on docker.ping() — background tasks keep the cache warm.
    return {
        "status": "ok",
        "service": "mcp-broker",
        "docker_ok": cached_docker_ok(),
    }
