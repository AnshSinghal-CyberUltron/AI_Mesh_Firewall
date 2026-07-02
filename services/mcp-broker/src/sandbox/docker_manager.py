"""Docker SDK lifecycle manager for per-org MCP sandbox containers."""

from __future__ import annotations

import logging
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sandbox.registry import SandboxRegistry

logger = logging.getLogger(__name__)

LABEL_ROLE = "ai_mesh.role"
LABEL_ORG_SLUG = "ai_mesh.org_slug"
ROLE_VALUE = "mcp-sandbox"
_DEFAULT_SANDBOX_USER = "sandbox"
_DEFAULT_EGRESS_PROXY = "http://host.docker.internal:3128"
_DEFAULT_NO_PROXY = "127.0.0.1,localhost,172.16.0.0/12,10.0.0.0/8"


class SandboxRuntimeUnavailableError(RuntimeError):
    """Raised when prod requires gVisor/runsc but Docker cannot provide it."""


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sandbox_ulimits(pids_limit: int) -> list[Any]:
    """FD + nproc caps for sandbox containers (dict fallback when docker SDK absent)."""
    try:
        from docker.types import Ulimit

        return [
            Ulimit(name="nofile", soft=1024, hard=2048),
            Ulimit(name="nproc", soft=pids_limit, hard=pids_limit),
        ]
    except ImportError:
        return [
            {"Name": "nofile", "Soft": 1024, "Hard": 2048},
            {"Name": "nproc", "Soft": pids_limit, "Hard": pids_limit},
        ]


def _security_opts() -> list[str]:
    """Layered sandbox security_opt: no-new-privileges + optional custom seccomp profile.

    When MCP_SANDBOX_SECCOMP_PROFILE is unset, Docker's built-in default seccomp profile
    still applies (we do not pass seccomp=unconfined).
    """
    opts = ["no-new-privileges:true"]
    profile = os.environ.get("MCP_SANDBOX_SECCOMP_PROFILE", "").strip()
    if profile:
        opts.append(f"seccomp={profile}")
    return opts


@dataclass(frozen=True)
class SandboxDockerConfig:
    image: str = "ai-mesh/mcp-sandbox:latest"
    network: str = "mcp_sandbox_bridge"
    agent_port: int = 9320
    memory_mb: int = 2048
    cpus: float = 1.0
    pids_limit: int = 256
    runtime: str | None = None
    runtime_required: bool = False
    sandbox_user: str = _DEFAULT_SANDBOX_USER
    egress_lockdown: bool = False
    http_proxy: str | None = None
    https_proxy: str | None = None
    no_proxy: str = _DEFAULT_NO_PROXY

    @classmethod
    def from_env(cls) -> SandboxDockerConfig:
        runtime = os.environ.get("MCP_SANDBOX_RUNTIME") or None
        http_proxy = os.environ.get("MCP_SANDBOX_HTTP_PROXY") or None
        https_proxy = os.environ.get("MCP_SANDBOX_HTTPS_PROXY") or None
        return cls(
            image=os.environ.get("MCP_SANDBOX_IMAGE", "ai-mesh/mcp-sandbox:latest"),
            network=os.environ.get("MCP_SANDBOX_NETWORK", "mcp_sandbox_bridge"),
            agent_port=int(os.environ.get("MCP_SANDBOX_AGENT_PORT", "9320")),
            memory_mb=int(os.environ.get("MCP_SANDBOX_MEMORY_MB", "2048")),
            cpus=float(os.environ.get("MCP_SANDBOX_CPUS", "1.0")),
            pids_limit=int(os.environ.get("MCP_SANDBOX_PIDS_LIMIT", "256")),
            runtime=runtime,
            runtime_required=_env_truthy("MCP_SANDBOX_RUNTIME_REQUIRED"),
            sandbox_user=os.environ.get("MCP_SANDBOX_USER", _DEFAULT_SANDBOX_USER),
            egress_lockdown=_env_truthy("MCP_SANDBOX_EGRESS_LOCKDOWN"),
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            no_proxy=os.environ.get("MCP_SANDBOX_NO_PROXY", _DEFAULT_NO_PROXY),
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

    def ping(self) -> bool:
        try:
            import docker

            probe = docker.from_env(timeout=5)
            probe.ping()
            return True
        except Exception:
            return False

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            # Sandbox lifecycle can exceed 10s when Docker is busy with parallel org sandboxes.
            self._client = docker.from_env(timeout=120)
        return self._client

    def container_name(self, org_slug: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") or "default"
        return f"{safe}-mcp-sandbox"

    def volume_name(self, org_slug: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", org_slug).strip("_") or "default"
        return f"mcp_sandbox_{safe}_auth"

    def org_network_name(self, org_slug: str) -> str:
        """Per-org Docker network — sandboxes cannot reach sibling agent ports."""
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") or "default"
        return f"mcp_sandbox_net_{safe}"

    def labels(self, org_slug: str) -> dict[str, str]:
        return {
            LABEL_ROLE: ROLE_VALUE,
            LABEL_ORG_SLUG: org_slug,
        }

    def find_container(self, org_slug: str) -> Any | None:
        by_name = self.get_container_by_name(org_slug)
        if by_name is not None:
            return by_name
        try:
            containers = self.client.containers.list(
                all=True,
                filters={
                    "label": [
                        f"{LABEL_ROLE}={ROLE_VALUE}",
                        f"{LABEL_ORG_SLUG}={org_slug}",
                    ],
                },
            )
            if containers:
                return containers[0]
        except Exception:
            pass
        return None

    def get_container_by_name(self, org_slug: str) -> Any | None:
        """Fallback when label filters miss a container that already owns the name."""
        try:
            return self.client.containers.get(self.container_name(org_slug))
        except Exception:
            return None

    def container_status(self, container: Any | None) -> str:
        if container is None:
            return "missing"
        state = getattr(container, "status", None)
        if not state:
            state = container.attrs.get("State", {}).get("Status", "stopped")
        return "running" if state == "running" else "stopped"

    def agent_url(self, container: Any, org_slug: str) -> str:
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        org_net = networks.get(self.org_network_name(org_slug), {})
        ip = org_net.get("IPAddress")
        if not ip:
            legacy = networks.get(self.config.network, {})
            ip = legacy.get("IPAddress") or container.attrs.get(
                "NetworkSettings", {}
            ).get("IPAddress", "")
        host = ip or "127.0.0.1"
        return f"http://{host}:{self.config.agent_port}"

    def _broker_container_ref(self) -> Any | None:
        explicit = os.environ.get("MCP_BROKER_CONTAINER_NAME", "").strip()
        if explicit:
            try:
                return self.client.containers.get(explicit)
            except Exception:
                pass
        try:
            return self.client.containers.get(socket.gethostname())
        except Exception:
            return None

    def _connect_broker_to_network(self, network: Any) -> None:
        broker = self._broker_container_ref()
        if broker is None:
            return
        try:
            network.connect(broker)
        except Exception as exc:
            if "already" in str(exc).lower():
                return
            raise

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
            agent_url=self.agent_url(container, org_slug) if status == "running" else "",
            name=container.name,
            created_at=created_at,
        )

    def ensure_network(self) -> None:
        """Ensure the legacy shared bridge exists (broker compose attachment)."""
        try:
            self.client.networks.get(self.config.network)
        except Exception:
            self.client.networks.create(
                self.config.network,
                driver="bridge",
                check_duplicate=True,
            )

    def runtime_available(self, runtime: str) -> bool:
        """Return True when Docker reports the requested OCI runtime."""
        try:
            info = self.client.info()
            runtimes = info.get("Runtimes") or {}
            return runtime in runtimes
        except Exception:
            return False

    def _resolve_runtime(self) -> str | None:
        runtime = self.config.runtime
        if not self.config.runtime_required:
            return runtime
        if not runtime:
            logger.error(
                "MCP_SANDBOX_RUNTIME_REQUIRED=true but MCP_SANDBOX_RUNTIME is unset"
            )
            raise SandboxRuntimeUnavailableError(
                "sandbox_runtime_unavailable: MCP_SANDBOX_RUNTIME is required but unset"
            )
        if not self.runtime_available(runtime):
            logger.error("Configured sandbox runtime %r is not available in Docker", runtime)
            raise SandboxRuntimeUnavailableError(
                f"sandbox_runtime_unavailable: runtime {runtime!r} is not available"
            )
        return runtime

    def _egress_proxy_env(self) -> dict[str, str]:
        if not self.config.egress_lockdown and not self.config.http_proxy:
            return {}
        http_proxy = self.config.http_proxy or _DEFAULT_EGRESS_PROXY
        https_proxy = self.config.https_proxy or http_proxy
        return {
            "HTTP_PROXY": http_proxy,
            "HTTPS_PROXY": https_proxy,
            "NO_PROXY": self.config.no_proxy,
        }

    @staticmethod
    def _assert_no_docker_socket_mount(volumes: dict[str, Any]) -> None:
        forbidden = {"/var/run/docker.sock", "/run/docker.sock"}
        for mount in volumes:
            if mount in forbidden or mount.endswith("docker.sock"):
                raise ValueError(f"refusing sandbox create: forbidden mount {mount!r}")

    def ensure_org_network(self, org_slug: str) -> str:
        """Create per-org network and attach the broker so it can reach the sandbox agent."""
        self.ensure_network()
        name = self.org_network_name(org_slug)
        try:
            network = self.client.networks.get(name)
        except Exception:
            try:
                network = self.client.networks.create(
                    name,
                    driver="bridge",
                    check_duplicate=True,
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status == 409 or "already exists" in str(exc).lower():
                    network = self.client.networks.get(name)
                else:
                    raise
        self._connect_broker_to_network(network)
        return name

    def _run_kwargs(self, org_slug: str) -> dict[str, Any]:
        runtime = self._resolve_runtime()
        org_net = self.ensure_org_network(org_slug)
        volume = self.volume_name(org_slug)
        mem_limit = f"{self.config.memory_mb}m"
        volumes = {volume: {"bind": "/data/mcp-auth", "mode": "rw"}}
        self._assert_no_docker_socket_mount(volumes)
        environment = {
            "ORG_SLUG": org_slug,
            "MCP_REMOTE_CONFIG_DIR": "/data/mcp-auth",
            "MCP_STDIO_MAX_PROCESSES_PER_ORG": os.environ.get(
                "MCP_STDIO_MAX_PROCESSES_PER_ORG", "16"
            ),
            # Sandbox USER is non-root; npm/uv caches need writable tmpfs (read_only rootfs).
            # npm cache MUST NOT live on noexec /tmp — npx bin symlinks are executed directly.
            "NPM_CONFIG_CACHE": "/var/npm-cache",
            # Kill postinstall lifecycle scripts during npx fetch (supply-chain RCE vector).
            "npm_config_ignore_scripts": "true",
            "UV_CACHE_DIR": "/var/cache/uv",
            "XDG_CACHE_HOME": "/var/cache",
        }
        environment.update(self._egress_proxy_env())
        kwargs: dict[str, Any] = {
            "image": self.config.image,
            "name": self.container_name(org_slug),
            "detach": True,
            "init": True,
            "labels": self.labels(org_slug),
            "environment": environment,
            "volumes": volumes,
            "network": org_net,
            "mem_limit": mem_limit,
            "memswap_limit": mem_limit,
            "nano_cpus": int(self.config.cpus * 1_000_000_000),
            "pids_limit": self.config.pids_limit,
            "read_only": True,
            "user": self.config.sandbox_user,
            "security_opt": _security_opts(),
            "cap_drop": ["ALL"],
            "ulimits": _sandbox_ulimits(self.config.pids_limit),
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=512m",
                "/var/npm-cache": "rw,exec,nosuid,size=1g,mode=1777",
                "/var/cache": "rw,exec,nosuid,size=512m,mode=1777",
            },
        }
        # Host-run broker (no container ref) needs published agent port on shared bridge.
        if self._broker_container_ref() is None:
            kwargs["ports"] = {f"{self.config.agent_port}/tcp": None}
            kwargs["network"] = self.config.network
        if runtime:
            kwargs["runtime"] = runtime
        return kwargs

    @staticmethod
    def _is_container_name_conflict(exc: Exception) -> bool:
        try:
            from docker.errors import APIError
        except ImportError:
            return False
        if isinstance(exc, APIError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 409:
                return True
        msg = str(exc).lower()
        return "already in use" in msg or "conflict" in msg

    def create_container(self, org_slug: str) -> Any:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self.client.containers.run(**self._run_kwargs(org_slug))
            except Exception as exc:
                last_exc = exc
                if not self._is_container_name_conflict(exc):
                    raise
                existing = self.get_container_by_name(org_slug)
                if existing is not None:
                    if self.container_status(existing) == "running":
                        return existing
                    try:
                        existing.remove(force=True)
                    except Exception:
                        pass
                if attempt < 2:
                    import time

                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"failed to create sandbox for {org_slug}")

    def _recover_broken_container(self, org_slug: str, container: Any) -> Any:
        try:
            container.remove(force=True)
        except Exception:
            pass
        return self.create_container(org_slug)

    def _start_or_recreate(self, org_slug: str, container: Any) -> Any:
        try:
            container.start()
            return container
        except Exception as exc:
            if not self._is_container_name_conflict(exc) and "marked for removal" not in str(
                exc
            ).lower():
                raise
            return self._recover_broken_container(org_slug, container)

    def ensure(self, org_slug: str) -> SandboxContainerInfo:
        # Broker must be on the per-org network even when reusing an existing sandbox.
        self.ensure_org_network(org_slug)
        container = self.find_container(org_slug)
        if container is None:
            container = self.create_container(org_slug)
            container.reload()
            info = self.to_info(org_slug, container)
            self._sync_registry(info)
            return info
        status = self.container_status(container)
        if status != "running":
            container = self._start_or_recreate(org_slug, container)
            container.reload()
        info = self.to_info(org_slug, container)
        self._sync_registry(info)
        return info

    def start(self, org_slug: str) -> SandboxContainerInfo:
        self.ensure_org_network(org_slug)
        container = self.find_container(org_slug)
        if container is None:
            container = self.create_container(org_slug)
            container.reload()
            info = self.to_info(org_slug, container)
            self._sync_registry(info)
            return info
        if self.container_status(container) != "running":
            container = self._start_or_recreate(org_slug, container)
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
