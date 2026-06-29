"""Docker SDK lifecycle manager for per-org MCP sandbox containers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sandbox.registry import SandboxRegistry

LABEL_ROLE = "ai_mesh.role"
LABEL_ORG_SLUG = "ai_mesh.org_slug"
ROLE_VALUE = "mcp-sandbox"


@dataclass(frozen=True)
class SandboxDockerConfig:
    image: str = "ai-mesh/mcp-sandbox:latest"
    network: str = "mcp_sandbox_bridge"
    agent_port: int = 9320
    memory_mb: int = 2048
    cpus: float = 1.0
    pids_limit: int = 256
    runtime: str | None = None

    @classmethod
    def from_env(cls) -> SandboxDockerConfig:
        runtime = os.environ.get("MCP_SANDBOX_RUNTIME") or None
        return cls(
            image=os.environ.get("MCP_SANDBOX_IMAGE", "ai-mesh/mcp-sandbox:latest"),
            network=os.environ.get("MCP_SANDBOX_NETWORK", "mcp_sandbox_bridge"),
            agent_port=int(os.environ.get("MCP_SANDBOX_AGENT_PORT", "9320")),
            memory_mb=int(os.environ.get("MCP_SANDBOX_MEMORY_MB", "2048")),
            cpus=float(os.environ.get("MCP_SANDBOX_CPUS", "1.0")),
            pids_limit=int(os.environ.get("MCP_SANDBOX_PIDS_LIMIT", "256")),
            runtime=runtime,
        )


@dataclass
class SandboxContainerInfo:
    org_slug: str
    container_id: str
    status: str
    agent_url: str
    name: str
    created_at: datetime | None = None


class DockerManager:
    """Create, start, stop, and destroy labeled sandbox containers per org_slug."""

    def __init__(
        self,
        client: Any | None = None,
        config: SandboxDockerConfig | None = None,
        registry: SandboxRegistry | None = None,
    ) -> None:
        self._client = client
        self.config = config or SandboxDockerConfig.from_env()
        self._registry = registry

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def container_name(self, org_slug: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") or "default"
        return f"mcp-sandbox-{safe}"

    def volume_name(self, org_slug: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", org_slug).strip("_") or "default"
        return f"mcp_sandbox_{safe}_auth"

    def labels(self, org_slug: str) -> dict[str, str]:
        return {
            LABEL_ROLE: ROLE_VALUE,
            LABEL_ORG_SLUG: org_slug,
        }

    def find_container(self, org_slug: str) -> Any | None:
        containers = self.client.containers.list(
            all=True,
            filters={
                "label": [
                    f"{LABEL_ROLE}={ROLE_VALUE}",
                    f"{LABEL_ORG_SLUG}={org_slug}",
                ],
            },
        )
        return containers[0] if containers else None

    def container_status(self, container: Any | None) -> str:
        if container is None:
            return "missing"
        state = getattr(container, "status", None)
        if not state:
            state = container.attrs.get("State", {}).get("Status", "stopped")
        return "running" if state == "running" else "stopped"

    def agent_url(self, container: Any) -> str:
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        net = networks.get(self.config.network, {})
        ip = net.get("IPAddress") or container.attrs.get("NetworkSettings", {}).get(
            "IPAddress", ""
        )
        host = ip or "127.0.0.1"
        return f"http://{host}:{self.config.agent_port}"

    def _sync_registry(self, info: SandboxContainerInfo) -> None:
        if self._registry is None:
            return
        if info.status == "running" and info.container_id:
            self._registry.register(
                org_slug=info.org_slug,
                container_id=info.container_id,
                agent_url=info.agent_url,
            )
        else:
            self._registry.remove(info.org_slug)

    def touch_activity(self, org_slug: str) -> None:
        if self._registry is not None:
            self._registry.touch(org_slug)

    def to_info(self, org_slug: str, container: Any | None) -> SandboxContainerInfo:
        status = self.container_status(container)
        if container is None:
            return SandboxContainerInfo(
                org_slug=org_slug,
                container_id="",
                status="missing",
                agent_url="",
                name=self.container_name(org_slug),
            )
        created_raw = container.attrs.get("Created")
        created_at = None
        if created_raw:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        return SandboxContainerInfo(
            org_slug=org_slug,
            container_id=container.id,
            status=status,
            agent_url=self.agent_url(container) if status == "running" else "",
            name=container.name,
            created_at=created_at,
        )

    def ensure_network(self) -> None:
        try:
            self.client.networks.get(self.config.network)
        except Exception:
            self.client.networks.create(
                self.config.network,
                driver="bridge",
                check_duplicate=True,
            )

    def _run_kwargs(self, org_slug: str) -> dict[str, Any]:
        volume = self.volume_name(org_slug)
        kwargs: dict[str, Any] = {
            "image": self.config.image,
            "name": self.container_name(org_slug),
            "detach": True,
            "labels": self.labels(org_slug),
            "environment": {
                "ORG_SLUG": org_slug,
                "MCP_REMOTE_CONFIG_DIR": "/data/mcp-auth",
                # Sandbox USER is non-root; npm/uv must cache under writable /tmp (read_only rootfs).
                "NPM_CONFIG_CACHE": "/tmp/.npm",
                "UV_CACHE_DIR": "/tmp/.cache/uv",
                "XDG_CACHE_HOME": "/tmp/.cache",
            },
            "volumes": {volume: {"bind": "/data/mcp-auth", "mode": "rw"}},
            "network": self.config.network,
            "mem_limit": f"{self.config.memory_mb}m",
            "nano_cpus": int(self.config.cpus * 1_000_000_000),
            "pids_limit": self.config.pids_limit,
            "read_only": True,
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=512m",
                "/root/.npm": "rw,size=1g",
                "/root/.cache/uv": "rw,size=512m",
            },
            "ports": {f"{self.config.agent_port}/tcp": None},
        }
        if self.config.runtime:
            kwargs["runtime"] = self.config.runtime
        return kwargs

    def create_container(self, org_slug: str) -> Any:
        self.ensure_network()
        return self.client.containers.run(**self._run_kwargs(org_slug))

    def ensure(self, org_slug: str) -> SandboxContainerInfo:
        container = self.find_container(org_slug)
        if container is None:
            container = self.create_container(org_slug)
            container.reload()
            info = self.to_info(org_slug, container)
            self._sync_registry(info)
            return info
        status = self.container_status(container)
        if status != "running":
            container.start()
            container.reload()
        info = self.to_info(org_slug, container)
        self._sync_registry(info)
        return info

    def start(self, org_slug: str) -> SandboxContainerInfo:
        container = self.find_container(org_slug)
        if container is None:
            container = self.create_container(org_slug)
            container.reload()
            info = self.to_info(org_slug, container)
            self._sync_registry(info)
            return info
        if self.container_status(container) != "running":
            container.start()
            container.reload()
        info = self.to_info(org_slug, container)
        self._sync_registry(info)
        return info

    def stop(self, org_slug: str) -> SandboxContainerInfo:
        container = self.find_container(org_slug)
        if container is None:
            info = self.to_info(org_slug, None)
            self._sync_registry(info)
            return info
        if self.container_status(container) == "running":
            container.stop(timeout=30)
            container.reload()
        info = self.to_info(org_slug, container)
        self._sync_registry(info)
        return info

    def destroy(self, org_slug: str) -> bool:
        container = self.find_container(org_slug)
        removed = False
        if container is not None:
            container.remove(force=True)
            removed = True
        volume_name = self.volume_name(org_slug)
        try:
            volume = self.client.volumes.get(volume_name)
            volume.remove(force=True)
            removed = True
        except Exception:
            pass
        if self._registry is not None:
            self._registry.remove(org_slug)
        return removed
