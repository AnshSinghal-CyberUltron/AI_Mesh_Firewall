"""Dedicated broker internal-auth edge cases — fail closed on missing/invalid key."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import BROKER_KEY_HEADER, expected_broker_key, require_broker_key
from sandbox.docker_manager import DockerManager, SandboxDockerConfig
from sandbox.registry import SandboxRegistry
from sandbox.routes import build_sandbox_router

BROKER_KEY = "test-broker-secret"
ORG = "auth-test-org"


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.containers.list.return_value = []
    client.networks.get.side_effect = Exception("not found")
    client.networks.create.return_value = MagicMock()
    client.volumes.get.side_effect = Exception("not found")
    return client


@pytest.fixture
def docker_manager() -> DockerManager:
    registry = SandboxRegistry(clock=lambda: 1_719_660_000.0)
    config = SandboxDockerConfig(memory_mb=2048, cpus=1.0)
    return DockerManager(client=_mock_client(), config=config, registry=registry)


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
    *,
    broker_key: str | None = BROKER_KEY,
) -> TestClient:
    if broker_key is None:
        monkeypatch.delenv("MCP_BROKER_INTERNAL_KEY", raising=False)
    else:
        monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", broker_key)
    test_app = FastAPI()
    test_app.include_router(build_sandbox_router(docker_manager))
    return TestClient(test_app)


def test_expected_broker_key_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MCP_BROKER_INTERNAL_KEY", raising=False)
    assert expected_broker_key() is None


def test_expected_broker_key_strips_whitespace(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", "  secret-key  ")
    assert expected_broker_key() == "secret-key"


def test_expected_broker_key_blank_is_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", "   ")
    assert expected_broker_key() is None


def test_require_broker_key_raises_without_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    with pytest.raises(Exception) as exc_info:
        require_broker_key(x_mcp_broker_key=None)
    assert getattr(exc_info.value, "status_code", None) == 401


def test_auth_missing_header_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
):
    with _build_client(monkeypatch, docker_manager) as client:
        resp = client.delete(f"/v1/sandbox/{ORG}")
    assert resp.status_code == 401
    assert "broker key" in resp.json()["detail"].lower()


def test_auth_wrong_key_returns_401(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
):
    with _build_client(monkeypatch, docker_manager) as client:
        resp = client.delete(
            f"/v1/sandbox/{ORG}",
            headers={BROKER_KEY_HEADER: "wrong-key"},
        )
    assert resp.status_code == 401
    assert "broker key" in resp.json()["detail"].lower()


def test_auth_unset_internal_key_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
):
    with _build_client(monkeypatch, docker_manager, broker_key=None) as client:
        resp = client.delete(
            f"/v1/sandbox/{ORG}",
            headers={BROKER_KEY_HEADER: BROKER_KEY},
        )
    assert resp.status_code == 401


def test_auth_blank_internal_key_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
):
    with _build_client(monkeypatch, docker_manager, broker_key="   ") as client:
        resp = client.delete(
            f"/v1/sandbox/{ORG}",
            headers={BROKER_KEY_HEADER: "any-key"},
        )
    assert resp.status_code == 401


def test_auth_valid_key_allows_protected_route(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
):
    with _build_client(monkeypatch, docker_manager) as client:
        resp = client.delete(
            f"/v1/sandbox/{ORG}",
            headers={BROKER_KEY_HEADER: BROKER_KEY},
        )
    assert resp.status_code == 200
    assert resp.json() == {"destroyed": True}
