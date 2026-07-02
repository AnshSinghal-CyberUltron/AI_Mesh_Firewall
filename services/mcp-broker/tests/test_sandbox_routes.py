"""Unit tests for Sandbox Controller REST routes (mocked Docker + httpx agent)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import BROKER_KEY_HEADER
from sandbox.docker_health import bind_docker_manager, cached_docker_ok
from sandbox.docker_manager import DockerManager, SandboxDockerConfig
from sandbox.registry import SandboxRegistry
from sandbox.routes import build_sandbox_router

BROKER_KEY = "test-broker-secret"
ORG = "acme"


def _mock_container(
    *,
    container_id: str = "abc123def456",
    status: str = "running",
    org_slug: str = ORG,
    ip: str = "172.28.0.42",
) -> MagicMock:
    container = MagicMock()
    container.id = container_id
    container.name = f"{org_slug}-mcp-sandbox"
    container.status = status
    container.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": status},
        "NetworkSettings": {
            "Networks": {
                f"mcp_sandbox_net_{org_slug}": {"IPAddress": ip},
                "mcp_sandbox_bridge": {"IPAddress": ip},
            },
        },
    }
    return container


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.info.return_value = {"Runtimes": {"runc": {}, "runsc": {}}}
    client.containers.list.return_value = []
    client.containers.get.side_effect = Exception("not found")
    client.networks.get.side_effect = Exception("not found")
    client.networks.create.return_value = MagicMock()
    client.volumes.get.side_effect = Exception("not found")
    return client


@pytest.fixture
def registry() -> SandboxRegistry:
    return SandboxRegistry(clock=lambda: 1_719_660_000.0)


@pytest.fixture
def docker_manager(registry: SandboxRegistry) -> DockerManager:
    config = SandboxDockerConfig(memory_mb=2048, cpus=1.0)
    manager = DockerManager(client=_mock_client(), config=config, registry=registry)
    bind_docker_manager(manager)
    cached_docker_ok(force=True)
    return manager


@pytest.fixture
def broker_client(
    monkeypatch: pytest.MonkeyPatch,
    docker_manager: DockerManager,
) -> TestClient:
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    test_app = FastAPI()
    test_app.include_router(build_sandbox_router(docker_manager))
    with TestClient(test_app) as client:
        yield client


def _auth_headers() -> dict[str, str]:
    return {BROKER_KEY_HEADER: BROKER_KEY}


def test_auth_rejects_missing_key(broker_client: TestClient):
    resp = broker_client.post(f"/v1/sandbox/{ORG}/ensure", json={"warm": True})
    assert resp.status_code == 401
    assert "broker key" in resp.json()["detail"].lower()


def test_auth_rejects_invalid_key(broker_client: TestClient):
    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"warm": True},
        headers={BROKER_KEY_HEADER: "wrong-key"},
    )
    assert resp.status_code == 401


def test_ensure_returns_running_sandbox(broker_client: TestClient, docker_manager: DockerManager):
    created = _mock_container()
    docker_manager.client.containers.run.return_value = created

    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"org_id": "uuid-1", "warm": True},
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["org_slug"] == ORG
    assert body["status"] == "running"
    assert body["container_id"] == created.id
    assert body["agent_url"] == "http://172.28.0.42:9320"
    assert body["created_at"].endswith("Z")
    assert body["last_activity"].endswith("Z")


def test_ensure_503_when_docker_unavailable(
    broker_client: TestClient,
    docker_manager: DockerManager,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "sandbox.routes.cached_docker_ok",
        lambda *args, **kwargs: False,
    )

    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"warm": True},
        headers=_auth_headers(),
    )

    assert resp.status_code == 503
    assert "docker" in resp.json()["detail"].lower()


def test_ensure_429_when_org_quota_exceeded(
    broker_client: TestClient,
    docker_manager: DockerManager,
    registry: SandboxRegistry,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MCP_SANDBOX_MAX_ORGS", "1")
    registry.register("other-org", "cid-other", "http://172.28.0.99:9320")

    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"warm": True},
        headers=_auth_headers(),
    )

    assert resp.status_code == 429
    docker_manager.client.containers.run.assert_not_called()


def test_stdio_rpc_forwards_to_agent_and_touches_activity(
    broker_client: TestClient,
    docker_manager: DockerManager,
    registry: SandboxRegistry,
):
    running = _mock_container()
    docker_manager.client.containers.list.return_value = [running]
    registry.register(ORG, running.id, "http://172.28.0.42:9320", last_activity=100.0)

    agent_response = httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": 42, "result": {"tools": []}},
        request=httpx.Request("POST", "http://172.28.0.42:9320/rpc"),
    )
    mock_http = AsyncMock()
    mock_http.post.return_value = agent_response
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    rpc_body = {
        "server_slug": "playwright",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "method": "tools/list",
        "jsonrpc_id": 42,
    }

    with patch("sandbox.routes.httpx.AsyncClient", return_value=mock_http):
        resp = broker_client.post(
            f"/v1/sandbox/{ORG}/stdio/rpc",
            json=rpc_body,
            headers=_auth_headers(),
        )

    assert resp.status_code == 200
    assert resp.json()["result"] == {"tools": []}
    mock_http.post.assert_called_once()
    call_args = mock_http.post.call_args
    assert call_args[0][0] == "http://172.28.0.42:9320/rpc"
    assert call_args[1]["json"]["server_slug"] == "playwright"
    entry = registry.get(ORG)
    assert entry is not None
    assert entry.last_activity == 1_719_660_000.0


def test_stdio_rpc_502_on_agent_unreachable(
    broker_client: TestClient,
    docker_manager: DockerManager,
):
    running = _mock_container()
    docker_manager.client.containers.list.return_value = [running]

    mock_http = AsyncMock()
    mock_http.post.side_effect = httpx.ConnectError("connection refused")
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch("sandbox.routes.httpx.AsyncClient", return_value=mock_http):
        resp = broker_client.post(
            f"/v1/sandbox/{ORG}/stdio/rpc",
            json={
                "server_slug": "stub",
                "command": "npx",
                "method": "tools/list",
                "jsonrpc_id": 1,
            },
            headers=_auth_headers(),
        )

    assert resp.status_code == 502


def test_status_running_sandbox(broker_client: TestClient, docker_manager: DockerManager):
    running = _mock_container()
    docker_manager.client.containers.list.return_value = [running]

    health_response = httpx.Response(
        200,
        json={
            "status": "ok",
            "processes": [
                {
                    "key": "playwright",
                    "running": True,
                    "initialized": True,
                    "last_used": 1_719_660_100,
                }
            ],
        },
        request=httpx.Request("GET", "http://172.28.0.42:9320/health"),
    )
    mock_http = AsyncMock()
    mock_http.get.return_value = health_response
    mock_http.__aenter__.return_value = mock_http
    mock_http.__aexit__.return_value = None

    with patch("sandbox.routes.httpx.AsyncClient", return_value=mock_http):
        resp = broker_client.get(
            f"/v1/sandbox/{ORG}/status",
            headers=_auth_headers(),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["org_slug"] == ORG
    assert body["status"] == "running"
    assert body["container_id"] == running.id
    assert body["processes"][0]["server_slug"] == "playwright"
    assert body["resource"]["cpu_limit"] == "1.0"
    assert body["resource"]["memory_limit_mb"] == 2048


def test_status_missing_sandbox(broker_client: TestClient, docker_manager: DockerManager):
    docker_manager.client.containers.list.return_value = []

    resp = broker_client.get(
        f"/v1/sandbox/ghost-org/status",
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "missing"
    assert body["container_id"] == ""
    assert body["processes"] == []


def test_destroy_is_idempotent(broker_client: TestClient, docker_manager: DockerManager):
    resp = broker_client.delete(
        f"/v1/sandbox/{ORG}",
        headers=_auth_headers(),
    )

    assert resp.status_code == 200
    assert resp.json() == {"destroyed": True}

    running = _mock_container()
    volume = MagicMock()
    docker_manager.client.containers.list.return_value = [running]
    docker_manager.client.volumes.get.side_effect = None
    docker_manager.client.volumes.get.return_value = volume

    resp2 = broker_client.delete(
        f"/v1/sandbox/{ORG}",
        headers=_auth_headers(),
    )

    assert resp2.status_code == 200
    running.remove.assert_called_once_with(force=True)
    volume.remove.assert_called_once_with(force=True)


def test_ensure_warm_reports_agent_ready(broker_client, docker_manager, monkeypatch):
    """B3 item#19: with warm=True and a bound agent, ensure blocks until the
    agent /health is OK and reports agent_ready=True / provisioning=False."""
    import sandbox.routes as routes

    created = _mock_container()
    docker_manager.client.containers.run.return_value = created
    monkeypatch.setattr(routes, "_warm_ready_timeout", lambda: 5.0)
    monkeypatch.setattr(routes, "_agent_health_ok", AsyncMock(return_value=True))

    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"warm": True},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["agent_ready"] is True
    assert body["provisioning"] is False


def test_ensure_warm_reports_provisioning_when_agent_not_ready(
    broker_client, docker_manager, monkeypatch
):
    """B3 item#19: when the agent socket never binds within the bounded warm
    window, ensure reports provisioning=True — a distinct 'still starting'
    state, NOT a hard 502."""
    import sandbox.routes as routes

    created = _mock_container()
    docker_manager.client.containers.run.return_value = created
    monkeypatch.setattr(routes, "_warm_ready_timeout", lambda: 0.2)
    monkeypatch.setattr(routes, "_warm_ready_interval", lambda: 0.01)
    monkeypatch.setattr(routes, "_agent_health_ok", AsyncMock(return_value=False))

    resp = broker_client.post(
        f"/v1/sandbox/{ORG}/ensure",
        json={"warm": True},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["provisioning"] is True
    assert body["agent_ready"] is False
