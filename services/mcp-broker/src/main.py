"""MCP broker scaffold — tool mediation and policy enforcement."""

from fastapi import FastAPI

app = FastAPI(title="AI Mesh MCP Broker", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mcp-broker"}
