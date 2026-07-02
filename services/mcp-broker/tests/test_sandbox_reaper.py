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
    client.info.return_value = {"Runtimes": {"runc": {}, "runsc": {}}}
    client.containers.list.return_value = []
    client.containers.get.side_effect = Exception("not found")
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
    running.name = "acme-mcp-sandbox"
    running.status = "running"
    running.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {
                "mcp_sandbox_net_acme": {"IPAddress": "172.28.0.42"},
                "mcp_sandbox_bridge": {"IPAddress": "172.28.0.42"},
            },
        },
    }
    manager.client.containers.list.return_value = [running]

    def _reload_stopped() -> None:
        running.status = "exited"
        running.attrs["State"]["Status"] = "exited"

    running.reload.side_effect = _reload_stopped

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


def test_ensure_registers_running_sandbox(manager: DockerManager, registry: SandboxRegistry):
    created = MagicMock()
    created.id = "new-id"
    created.name = "beta-mcp-sandbox"
    created.status = "running"
    created.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {
                "mcp_sandbox_net_beta": {"IPAddress": "172.28.0.99"},
                "mcp_sandbox_bridge": {"IPAddress": "172.28.0.99"},
            },
        },
    }
    manager.client.containers.run.return_value = created
    created.reload.side_effect = lambda: None

    info = manager.ensure("beta")

    entry = registry.get("beta")
    assert entry is not None
    assert entry.container_id == info.container_id == "new-id"
    assert entry.agent_url == "http://172.28.0.99:9320"


def test_reconcile_registry_adopts_orphan(manager: DockerManager, registry: SandboxRegistry):
    # item #24: a running sandbox container the registry lost track of (e.g. after
    # a broker restart) is re-adopted so it's tracked + eventually reaped.
    from sandbox.docker_manager import LABEL_ORG_SLUG, LABEL_ROLE, ROLE_VALUE

    orphan = MagicMock()
    orphan.id = "orphan-id"
    orphan.name = "gamma-mcp-sandbox"
    orphan.status = "running"
    orphan.labels = {LABEL_ROLE: ROLE_VALUE, LABEL_ORG_SLUG: "gamma"}
    orphan.attrs = {
        "Created": "2026-06-29T12:00:00.000000000Z",
        "State": {"Status": "running"},
        "NetworkSettings": {
            "Networks": {
                "mcp_sandbox_net_gamma": {"IPAddress": "172.28.0.77"},
                "mcp_sandbox_bridge": {"IPAddress": "172.28.0.77"},
            },
        },
    }
    manager.client.containers.list.return_value = [orphan]

    assert registry.get("gamma") is None
    adopted = manager.reconcile_registry()
    assert adopted == 1
    entry = registry.get("gamma")
    assert entry is not None
    assert entry.container_id == "orphan-id"
    assert entry.agent_url == "http://172.28.0.77:9320"
    # idempotent: already tracked → not re-adopted
    assert manager.reconcile_registry() == 0


@pytest.mark.asyncio
async def test_reaper_survives_stop_error(
    manager: DockerManager, registry: SandboxRegistry, clock: MockClock
):
    # item #24: a transient Docker error stopping one sandbox must NOT propagate
    # (which would kill the reaper loop) — the entry is kept for retry.
    registry.register("acme", "abc123", "http://172.28.0.42:9320", last_activity=clock() - 700)
    manager.stop = MagicMock(side_effect=RuntimeError("docker APIError"))

    stopped = await reap_idle_sandboxes(
        registry,
        manager,
        config=ReaperConfig(idle_timeout=600, interval_seconds=1),
        now=clock(),
    )

    assert stopped == []  # stop failed → not counted
    assert registry.get("acme") is not None  # entry kept for the next sweep


def test_reaper_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_IDLE_TIMEOUT", "900")
    monkeypatch.setenv("MCP_SANDBOX_REAPER_INTERVAL", "30")

    config = ReaperConfig.from_env()

    assert config.idle_timeout == 900
    assert config.interval_seconds == 30


@pytest.mark.asyncio
async def test_reaper_multi_org_partial_failure(
    manager: DockerManager, registry: SandboxRegistry, clock: MockClock
):
    # item #24: when one org's stop raises, the other idle orgs are still reaped
    # (per-entry guard in reap_idle_sandboxes isolates failures).
    registry.register("failing-org", "cid-fail", "http://1:9320", last_activity=clock() - 700)
    registry.register("good-org", "cid-good", "http://2:9320", last_activity=clock() - 700)

    good_container = MagicMock()
    good_container.id = "cid-good"
    good_container.name = "good-org-mcp-sandbox"
    good_container.status = "running"
    good_container.attrs = {
        "Created": "2026-06-29T12:00:00Z",
        "State": {"Status": "running"},
        "NetworkSettings": {"Networks": {"mcp_sandbox_bridge": {"IPAddress": "172.28.0.2"}}},
    }

    fail_container = MagicMock()
    fail_container.id = "cid-fail"
    fail_container.name = "failing-org-mcp-sandbox"
    fail_container.status = "running"
    fail_container.attrs = good_container.attrs.copy()
    fail_container.stop.side_effect = RuntimeError("Docker APIError: connection reset")

    def _find(org_slug: str) -> MagicMock | None:
        return fail_container if org_slug == "failing-org" else good_container

    manager.client.containers.list.return_value = [good_container, fail_container]

    original_stop = manager.stop.__func__ if hasattr(manager.stop, "__func__") else None

    def _stop_or_raise(org: str) -> None:
        if org == "failing-org":
            raise RuntimeError("Docker APIError: connection reset")
        good_container.reload.side_effect = lambda: None
        good_container.stop()

    manager.stop = MagicMock(side_effect=_stop_or_raise)

    stopped = await reap_idle_sandboxes(
        registry,
        manager,
        config=ReaperConfig(idle_timeout=600, interval_seconds=1),
        now=clock(),
    )

    assert "good-org" in stopped
    assert "failing-org" not in stopped
    assert registry.get("good-org") is None
    assert registry.get("failing-org") is not None


@pytest.mark.asyncio
async def test_reaper_loop_survives_reconcile_error(
    manager: DockerManager, registry: SandboxRegistry
):
    # item #24: an exception raised inside reconcile_registry must NOT kill the
    # reaper task — the outer try/except in _reaper_loop catches it and logs,
    # so the next tick can succeed.
    from sandbox.reaper import _reaper_loop

    manager.reconcile_registry = MagicMock(side_effect=RuntimeError("docker down"))

    swept = []

    async def _one_tick_loop(
        registry: SandboxRegistry,
        docker_manager: DockerManager,
        config: ReaperConfig,
    ) -> None:
        import asyncio as _asyncio

        await _asyncio.sleep(0)
        try:
            await _asyncio.to_thread(docker_manager.reconcile_registry)
            await reap_idle_sandboxes(registry, docker_manager, config=config)
        except Exception:
            swept.append("exception-swallowed")
            return
        swept.append("completed")

    cfg = ReaperConfig(idle_timeout=600, interval_seconds=0)
    await _one_tick_loop(registry, manager, cfg)

    assert swept == ["exception-swallowed"]
    # registry is intact (not corrupted by the error)
    assert registry.list() == []
