"""Unit tests for per-org sandbox Docker lifecycle (mocked Docker SDK)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sandbox.docker_manager import (
    LABEL_ORG_SLUG,
    LABEL_ROLE,
    ROLE_VALUE,
    DockerManager,
    SandboxDockerConfig,
)


def _mock_container(
    *,
    container_id: str = "abc123def456",
    status: str = "running",
    org_slug: str = "acme",
    ip: str = "172.28.0.42",
    created: str = "2026-06-29T12:00:00.000000000Z",
) -> MagicMock:
    org_net = f"mcp_sandbox_net_{org_slug}"
    container = MagicMock()
    container.id = container_id
    container.name = f"mcp-sandbox-{org_slug}"
    container.status = status
    container.attrs = {
        "Created": created,
        "State": {"Status": status},
        "NetworkSettings": {
            "Networks": {
                org_net: {"IPAddress": ip},
                "mcp_sandbox_bridge": {"IPAddress": ip},
            },
        },
    }
    return container


def _mock_client() -> MagicMock:
    client = MagicMock()
    client.ping.return_value = True
    client.containers.list.return_value = []
    client.containers.get.side_effect = Exception("not found")
    client.networks.get.side_effect = Exception("not found")
    client.networks.create.return_value = MagicMock()
    client.volumes.get.side_effect = Exception("not found")
    return client


@pytest.fixture
def manager() -> DockerManager:
    config = SandboxDockerConfig(
        image="ai-mesh/mcp-sandbox:test",
        network="mcp_sandbox_bridge",
        agent_port=9320,
        memory_mb=1024,
        cpus=0.5,
        pids_limit=128,
    )
    return DockerManager(client=_mock_client(), config=config)


def test_labels_include_role_and_org(manager: DockerManager):
    labels = manager.labels("acme-corp")
    assert labels[LABEL_ROLE] == ROLE_VALUE
    assert labels[LABEL_ORG_SLUG] == "acme-corp"


def test_ensure_creates_container_when_missing(manager: DockerManager):
    created = _mock_container(org_slug="acme")
    manager.client.containers.run.return_value = created

    info = manager.ensure("acme")

    manager.client.containers.run.assert_called_once()
    run_kwargs = manager.client.containers.run.call_args.kwargs
    assert run_kwargs["image"] == "ai-mesh/mcp-sandbox:test"
    assert run_kwargs["labels"][LABEL_ROLE] == ROLE_VALUE
    assert run_kwargs["labels"][LABEL_ORG_SLUG] == "acme"
    assert run_kwargs["mem_limit"] == "1024m"
    assert run_kwargs["nano_cpus"] == 500_000_000
    assert run_kwargs["pids_limit"] == 128
    assert run_kwargs["ports"] == {"9320/tcp": None}
    assert run_kwargs["network"] == "mcp_sandbox_bridge"
    assert run_kwargs["environment"]["ORG_SLUG"] == "acme"
    assert info.status == "running"
    assert info.container_id == created.id
    assert info.agent_url == "http://172.28.0.42:9320"


def test_ensure_starts_stopped_container(manager: DockerManager):
    stopped = _mock_container(status="exited", org_slug="acme")
    stopped.status = "exited"
    stopped.attrs["State"]["Status"] = "exited"
    manager.client.containers.list.return_value = [stopped]

    def _reload_running():
        stopped.status = "running"
        stopped.attrs["State"]["Status"] = "running"

    stopped.reload.side_effect = _reload_running

    info = manager.ensure("acme")

    stopped.start.assert_called_once()
    manager.client.containers.run.assert_not_called()
    assert info.status == "running"


def test_ensure_reuses_running_container(manager: DockerManager):
    running = _mock_container(org_slug="acme")
    manager.client.containers.list.return_value = [running]

    info = manager.ensure("acme")

    running.start.assert_not_called()
    manager.client.containers.run.assert_not_called()
    assert info.status == "running"
    assert info.agent_url == "http://172.28.0.42:9320"


def test_start_creates_when_missing(manager: DockerManager):
    created = _mock_container(org_slug="beta")
    manager.client.containers.run.return_value = created

    info = manager.start("beta")

    manager.client.containers.run.assert_called_once()
    assert info.status == "running"


def test_stop_stops_running_container(manager: DockerManager):
    running = _mock_container(org_slug="acme")
    manager.client.containers.list.return_value = [running]
    running.reload.side_effect = lambda: setattr(running, "status", "exited")

    def _reload_stopped():
        running.status = "exited"
        running.attrs["State"]["Status"] = "exited"

    running.reload.side_effect = _reload_stopped

    info = manager.stop("acme")

    running.stop.assert_called_once_with(timeout=30)
    assert info.status == "stopped"
    assert info.agent_url == ""


def test_stop_missing_is_idempotent(manager: DockerManager):
    info = manager.stop("missing-org")
    assert info.status == "missing"
    assert info.container_id == ""


def test_destroy_removes_container_and_volume(manager: DockerManager):
    running = _mock_container(org_slug="acme")
    volume = MagicMock()
    manager.client.containers.list.return_value = [running]
    manager.client.volumes.get.side_effect = None
    manager.client.volumes.get.return_value = volume

    removed = manager.destroy("acme")

    running.remove.assert_called_once_with(force=True)
    volume.remove.assert_called_once_with(force=True)
    assert removed is True


def test_destroy_missing_is_idempotent(manager: DockerManager):
    removed = manager.destroy("ghost-org")
    assert removed is False


def test_ensure_network_created_once(manager: DockerManager):
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")

    manager.ensure("acme")

    manager.client.networks.create.assert_called()
    created_names = [c.args[0] for c in manager.client.networks.create.call_args_list]
    assert "mcp_sandbox_net_acme" in created_names


def test_per_org_network_name(manager: DockerManager):
    assert manager.org_network_name("adv-org-alpha") == "mcp_sandbox_net_adv-org-alpha"


def test_config_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_IMAGE", "custom/sandbox:v2")
    monkeypatch.setenv("MCP_SANDBOX_MEMORY_MB", "4096")
    monkeypatch.setenv("MCP_SANDBOX_CPUS", "2.5")
    monkeypatch.setenv("MCP_SANDBOX_RUNTIME", "runsc")

    config = SandboxDockerConfig.from_env()

    assert config.image == "custom/sandbox:v2"
    assert config.memory_mb == 4096
    assert config.cpus == 2.5
    assert config.runtime == "runsc"
