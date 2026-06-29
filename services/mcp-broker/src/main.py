"""MCP broker scaffold — tool mediation and policy enforcement."""

from fastapi import FastAPI

from sandbox.docker_manager import DockerManager

app = FastAPI(title="AI Mesh MCP Broker", version="0.1.0")
docker_manager = DockerManager()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mcp-broker"}
