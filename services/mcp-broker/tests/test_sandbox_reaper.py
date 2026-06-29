"""Unit tests for sandbox registry and idle reaper (mock clock)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sandbox.docker_manager import DockerManager, SandboxDockerConfig
from sandbox.registry import SandboxRegistry
from sandbox.reaper import ReaperConfig, reap_idle_sandboxes


class MockClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.containers.list.return_value = []
    client.networks.get.side_effect = Exception("not found")
    client.networks.create.return_value = MagicMock()
    client.volumes.get.side_effect = Exception("not found")
    return client


@pytest.fixture
def clock() -> MockClock:
    return MockClock()


@pytest.fixture
def registry(clock: MockClock) -> SandboxRegistry:
    return SandboxRegistry(clock=clock)


@pytest.fixture
def manager(registry: SandboxRegistry) -> DockerManager:
    config = SandboxDockerConfig(
        image="ai-mesh/mcp-sandbox:test",
        network="mcp_sandbox_bridge",
        agent_port=9320,
    )
    return DockerManager(client=_mock_client(), config=config, registry=registry)


def test_registry_register_and_touch(clock: MockClock, registry: SandboxRegistry):
    entry = registry.register("acme", "cid-1", "http://172.0.0.1:9320")
    assert entry.org_slug == "acme"
    assert entry.container_id == "cid-1"
    assert entry.last_activity == clock()

    clock.advance(30)
    touched = registry.touch("acme")
    assert touched is not None
    assert touched.last_activity == clock()

    assert registry.get("acme") is touched
    assert len(registry.list()) == 1


def test_registry_idle_entries(clock: MockClock, registry: SandboxRegistry):
    registry.register("fresh", "c1", "http://1:9320")
    clock.advance(100)
    registry.register("stale", "c2", "http://2:9320", last_activity=clock() - 700)

    idle = registry.idle_entries(idle_timeout=600, now=clock())
    assert [entry.org_slug for entry in idle] == ["stale"]


@pytest.mark.asyncio
async def test_reaper_stops_idle_container(manager: DockerManager, registry: SandboxRegistry, clock: MockClock):
    running = MagicMock()
    running.id = "abc123"
    running.name = "mcp-sandbox-acme"
    running.status = "running"
    running.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {"mcp_sandbox_bridge": {"IPAddress": "172.28.0.42"}},
        },
    }
    manager.client.containers.list.return_value = [running]

    registry.register(
        "acme",
        "abc123",
        "http://172.28.0.42:9320",
        last_activity=clock() - 700,
    )

    stopped = await reap_idle_sandboxes(
        registry,
        manager,
        config=ReaperConfig(idle_timeout=600, interval_seconds=1),
        now=clock(),
    )

    assert stopped == ["acme"]
    running.stop.assert_called_once_with(timeout=30)
    assert registry.get("acme") is None


@pytest.mark.asyncio
async def test_reaper_keeps_active_container(manager: DockerManager, registry: SandboxRegistry, clock: MockClock):
    registry.register("acme", "abc123", "http://172.28.0.42:9320")
    clock.advance(120)

    stopped = await reap_idle_sandboxes(
        registry,
        manager,
        config=ReaperConfig(idle_timeout=600, interval_seconds=1),
        now=clock(),
    )

    assert stopped == []
    manager.client.containers.list.assert_not_called()
    assert registry.get("acme") is not None


@pytest.mark.asyncio
async def test_ensure_registers_running_sandbox(manager: DockerManager, registry: SandboxRegistry):
    created = MagicMock()
    created.id = "new-id"
    created.name = "mcp-sandbox-beta"
    created.status = "running"
    created.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {"mcp_sandbox_bridge": {"IPAddress": "172.28.0.99"}},
        },
    }
    manager.client.containers.run.return_value = created

    info = manager.ensure("beta")

    entry = registry.get("beta")
    assert entry is not None
    assert entry.container_id == info.container_id == "new-id"
    assert entry.agent_url == "http://172.28.0.99:9320"


def test_reaper_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_IDLE_TIMEOUT", "900")
    monkeypatch.setenv("MCP_SANDBOX_REAPER_INTERVAL", "30")

    config = ReaperConfig.from_env()

    assert config.idle_timeout == 900
    assert config.interval_seconds == 30
