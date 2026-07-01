"""Validate docker-compose sandbox wiring for mcp-broker (S9 gate)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
BROKER_DOCKERFILE = REPO_ROOT / "services/mcp-broker/Dockerfile"


def test_broker_dockerfile_exists_and_declares_docker_sdk():
    text = BROKER_DOCKERFILE.read_text()
    assert "sandbox-image/Dockerfile" in text
    assert "EXPOSE 8311" in text
    assert "uvicorn" in text


@pytest.mark.docker
def test_compose_services_profile_config_validates():
    """Gate: docker compose --profile services config must succeed."""
    if not shutil.which("docker"):
        pytest.skip("docker not available")

    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "services",
            "config",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    rendered = proc.stdout
    assert "mcp_sandbox_bridge" in rendered
    assert "/var/run/docker.sock" in rendered
    assert "MCP_BROKER_INTERNAL_KEY" in rendered
    assert "MCP_STDIO_IN_PROCESS" in rendered
    assert "mcp-broker" in rendered
    assert "services/mcp-broker/Dockerfile" in rendered
    assert "services/mcp-broker/sandbox-image/Dockerfile" in rendered
    assert "gateway:" in rendered
    gateway_section = rendered.split("gateway:", 1)[1].split("\n  ", 1)[0]
    assert "docker.sock" not in gateway_section
