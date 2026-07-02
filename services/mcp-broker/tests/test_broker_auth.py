"""Dedicated broker internal-auth edge cases — fail closed on missing/invalid key."""

from __future__ import annotations

import logging
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


# ── CHG-0052: the broker LOGS the propagated X-Request-ID (completes CHG-0051) ──


def test_rpc_logs_x_request_id(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
    caplog: pytest.LogCaptureFixture,
):
    # The correlation id is logged BEFORE docker/sandbox resolution. Force the clean
    # early 503 (docker unavailable) so the log-under-test isn't masked by unrelated
    # mock-docker resolution details / cross-test cached_docker_ok state.
    monkeypatch.setattr("sandbox.routes.cached_docker_ok", lambda: False)
    client = _build_client(monkeypatch, docker_manager)
    with caplog.at_level(logging.INFO, logger="mcp_broker.sandbox_rpc"):
        client.post(
            f"/v1/sandbox/{ORG}/rpc",
            headers={BROKER_KEY_HEADER: BROKER_KEY, "X-Request-ID": "trace-broker-42"},
            json={
                "server_slug": "srv", "transport": "stdio",
                "method": "tools/call", "jsonrpc_id": 1,
            },
        )
    msgs = [r.getMessage() for r in caplog.records if r.name == "mcp_broker.sandbox_rpc"]
    assert any("request_id=trace-broker-42" in m for m in msgs)      # trace id logged
    assert any("method=tools/call" in m for m in msgs)               # safe metadata logged
    assert all("params" not in m for m in msgs)                      # no raw payload in logs


def test_rpc_logs_dash_when_no_x_request_id(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr("sandbox.routes.cached_docker_ok", lambda: False)
    client = _build_client(monkeypatch, docker_manager)
    with caplog.at_level(logging.INFO, logger="mcp_broker.sandbox_rpc"):
        client.post(
            f"/v1/sandbox/{ORG}/rpc",
            headers={BROKER_KEY_HEADER: BROKER_KEY},
            json={"server_slug": "srv", "method": "ping", "jsonrpc_id": 2},
        )
    msgs = [r.getMessage() for r in caplog.records if r.name == "mcp_broker.sandbox_rpc"]
    assert any("request_id=-" in m for m in msgs)                    # no header -> "-" placeholder
