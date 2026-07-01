"""Tests for the sandbox image and in-container agent."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
SANDBOX_IMAGE = REPO_ROOT / "services/mcp-broker/sandbox-image"
DOCKERFILE = REPO_ROOT / "services/mcp-broker/sandbox-image/Dockerfile"


def _load_agent_app(monkeypatch):
    monkeypatch.setenv("ORG_SLUG", "test-org")
    sandbox_image = str(SANDBOX_IMAGE)
    if sandbox_image not in sys.path:
        sys.path.insert(0, sandbox_image)
    for mod in ("agent.main", "agent.stdio_manager", "agent"):
        sys.modules.pop(mod, None)
    return importlib.import_module("agent.main").app


@pytest.fixture
def agent_client(monkeypatch):
    app = _load_agent_app(monkeypatch)
    with TestClient(app) as client:
        yield client


def test_dockerfile_exists_and_declares_port():
    text = DOCKERFILE.read_text()
    assert "node:20-bookworm-slim" in text
    assert "python:3.12" in text
    assert "EXPOSE 9320" in text
    assert "uvicorn" in text
    assert "agent.main:app" in text


def test_agent_health(agent_client):
    resp = agent_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "mcp-sandbox-agent"
    assert body["org_slug"] == "test-org"


def test_agent_rpc_missing_command_returns_error(agent_client):
    resp = agent_client.post(
        "/rpc",
        json={
            "server_slug": "stub",
            "command": "bash",
            "args": ["-c", "echo hi"],
            "method": "tools/list",
            "jsonrpc_id": 7,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 7
    assert "error" in body


@pytest.mark.docker
def test_sandbox_image_builds():
    """Gate for S1: docker build must succeed (skipped when Docker unavailable)."""
    import shutil
    import subprocess

    if not shutil.which("docker"):
        pytest.skip("docker not available")

    tag = "ai-mesh/mcp-sandbox:test"
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            tag,
            "-f",
            str(DOCKERFILE),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
