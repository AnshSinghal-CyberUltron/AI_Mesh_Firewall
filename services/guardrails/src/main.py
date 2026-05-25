"""Guardrails service scaffold — input/output/context scanning."""

from fastapi import FastAPI

app = FastAPI(title="AI Mesh Guardrails", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "guardrails"}


@app.post("/v1/scan/input")
def scan_input() -> dict:
    return {"action": "allow", "note": "migrate from gateway scanner"}


@app.post("/v1/scan/output")
def scan_output() -> dict:
    return {"action": "allow", "note": "migrate from gateway output guard"}
