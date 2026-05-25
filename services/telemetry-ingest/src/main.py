"""Telemetry ingest scaffold — high-volume append-only events."""

from fastapi import FastAPI

app = FastAPI(title="AI Mesh Telemetry Ingest", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "telemetry-ingest"}
