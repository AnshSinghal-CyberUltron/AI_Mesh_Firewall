"""MCP broker scaffold — tool mediation and policy enforcement."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from sandbox.docker_manager import DockerManager
from sandbox.registry import SandboxRegistry
from sandbox.reaper import start_reaper, stop_reaper

sandbox_registry = SandboxRegistry()
docker_manager = DockerManager(registry=sandbox_registry)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_reaper(sandbox_registry, docker_manager)
    yield
    await stop_reaper()


app = FastAPI(title="AI Mesh MCP Broker", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mcp-broker"}
