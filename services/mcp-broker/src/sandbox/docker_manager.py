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


def _sandbox_ulimits() -> list[Any]:
    """Per-process FD cap for sandbox containers (dict fallback when docker SDK absent).

    We deliberately do NOT set an ``nproc`` ulimit. ``RLIMIT_NPROC`` is enforced
    by the kernel per *host UID*, and every org sandbox runs as the same
    ``sandbox`` user (uid 1000), so an nproc ulimit becomes ONE process budget
    SHARED across all tenants. That (a) breaks multi-org scaling — once the shared
    budget fills, ~half of the per-org stdio servers fail to ``fork`` with EAGAIN
    ("resource temporarily unavailable") and surface as 0-tools — and (b) lets one
    org starve another's ability to fork (a cross-tenant DoS on a shared limit).
    Per-container process/thread containment is instead provided by ``pids_limit``
    (the pids cgroup controller), which IS container-scoped and already caps
    fork bombs per sandbox. See mcp-parallel/findings/p8-26/NPROC_ROOT_CAUSE.md.
    """
    try:
        from docker.types import Ulimit

        return [Ulimit(name="nofile", soft=1024, hard=2048)]
    except ImportError:
        return [{"Name": "nofile", "Soft": 1024, "Hard": 2048}]


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
    # Node/V8 old-space heap cap for spawned MCP servers (MB). Set BELOW the
    # container mem_limit so a memory-hungry Node server (e.g. a heavy stdio MCP)
    # hits a graceful, catchable V8 "heap out of memory" instead of the kernel
    # OOM-killing the whole cgroup with SIGKILL (exit -9 / 137). 0 = derive as
    # ~75% of memory_mb. Also stops one runaway process starving the shared
    # per-org sandbox. (CP20)
    node_max_old_space_mb: int = 0
    # RAM-backed npm-cache tmpfs size (MB). Heavy servers with large dependency
    # trees (Ruflo) overflow the default 1024 and fail with ENOSPC. (CP21)
    npm_cache_size_mb: int = 1024
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
            node_max_old_space_mb=int(os.environ.get("MCP_SANDBOX_NODE_MAX_OLD_SPACE_MB", "0")),
            npm_cache_size_mb=int(os.environ.get("MCP_SANDBOX_NPM_CACHE_SIZE_MB", "1024")),
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


def _gvisor_dns_servers() -> list[str]:
    """Explicit DNS for gVisor sandboxes.

    Docker's embedded resolver (127.0.0.11) is unreliable under runsc; stdio MCP
    servers that npx-fetch from registry.npmjs.org fail with EAI_AGAIN without this.
    Override via MCP_SANDBOX_DNS (comma-separated).
    """
    override = os.environ.get("MCP_SANDBOX_DNS", "").strip()
    if override:
        return [s.strip() for s in override.split(",") if s.strip()]
    return ["8.8.8.8", "8.8.4.4"]


# Transport-stub sidecars attach to mcp_sandbox_net_<org> with DNS aliases; gVisor
# cannot use Docker embedded DNS (127.0.0.11) on user-defined bridges (F-015).
_STUB_HOST_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("http-everything.stub", ("http-everything", "ai_mesh_firewall-http-everything-1")),
    ("sse-everything.stub", ("sse-everything", "ai_mesh_firewall-sse-everything-1")),
    ("ws-everything.stub", ("ws-everything", "ai_mesh_firewall-ws-everything-1")),
)


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
        # CHG-0143: warn only once about a degraded (non-gVisor) runtime posture, so the
        # signal is loud at startup without spamming a line per sandbox create.
        self._runtime_degraded_warned = False

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

    @staticmethod
    def _container_labels(container: Any) -> dict:
        """Best-effort label dict for a container (SDK ``.labels`` or raw attrs)."""
        labels = getattr(container, "labels", None)
        if not isinstance(labels, dict):
            labels = (getattr(container, "attrs", {}) or {}).get("Config", {}).get("Labels", {}) or {}
        return labels if isinstance(labels, dict) else {}

    @staticmethod
    def _volume_labels(volume: Any) -> dict:
        """Best-effort label dict for a volume (``volume.attrs['Labels']``, may be None)."""
        labels = (getattr(volume, "attrs", {}) or {}).get("Labels")
        return labels if isinstance(labels, dict) else {}

    def _ensure_volume(self, org_slug: str) -> None:
        """Create the org's auth volume WITH the org label so ``destroy`` can verify
        tenancy (CHG-0113).

        The volume was previously auto-created (unlabeled) by the container run, so
        ``destroy`` could only match it by its LOSSY-sanitized name — the same
        cross-tenant fragility CHG-0112 fixed for containers. Creating it explicitly
        with ``labels(org_slug)`` makes the org label the authoritative key for the
        volume too. Idempotent + best-effort: a pre-existing volume is left as-is
        (Docker won't relabel); a failure here never blocks provisioning because the
        container run still auto-creates the volume by name.
        """
        name = self.volume_name(org_slug)
        try:
            self.client.volumes.get(name)
            return  # already exists (labeled or legacy) — leave as-is
        except Exception:
            pass
        try:
            self.client.volumes.create(name=name, labels=self.labels(org_slug))
        except Exception as exc:  # pragma: no cover - defensive; run auto-creates anyway
            logger.debug("sandbox volume pre-create skipped for %s: %s", org_slug, exc)

    def get_container_by_name(self, org_slug: str) -> Any | None:
        """Look up the org's sandbox by its deterministic name — but VERIFY the org
        label before returning it (CHG-0112).

        The container NAME is derived by a LOSSY sanitizer (see ``container_name``), so a
        name match is NOT proof of tenancy: a legacy/renamed/reused container that owns
        the name but carries a DIFFERENT ``LABEL_ORG_SLUG`` (e.g. a pre-CHG-0111 container
        created for a colliding slug like ``acme/prod`` still owning ``…-acme-prod``) would
        otherwise be returned for the WRONG org — a cross-tenant hazard. The org LABEL is
        the authoritative tenant key; on a label mismatch return None (fail closed) so the
        caller falls through to the label-filtered lookup, which is authoritative.
        """
        try:
            container = self.client.containers.get(self.container_name(org_slug))
        except Exception:
            return None
        labels = self._container_labels(container)
        if labels.get(LABEL_ORG_SLUG) != org_slug or labels.get(LABEL_ROLE) != ROLE_VALUE:
            logger.warning(
                "sandbox name/label mismatch: name=%s requested_org=%s labeled_org=%s "
                "role=%s (ignoring by-name match, fail-closed)",
                self.container_name(org_slug), org_slug,
                labels.get(LABEL_ORG_SLUG), labels.get(LABEL_ROLE),
            )
            return None
        return container

    def list_sandbox_containers(self) -> list[tuple[str, Any]]:
        """(org_slug, container) for every sandbox container (any org) by role label.

        Used to reconcile the in-memory registry with live Docker state after a
        broker restart (re-adopt orphans) — item #24 restart-safety.
        """
        out: list[tuple[str, Any]] = []
        try:
            containers = self.client.containers.list(
                all=True, filters={"label": [f"{LABEL_ROLE}={ROLE_VALUE}"]}
            )
        except Exception:
            return out
        for c in containers:
            org = self._container_labels(c).get(LABEL_ORG_SLUG)
            if org:
                out.append((org, c))
        return out

    def reconcile_registry(self) -> int:
        """Re-adopt live RUNNING sandbox containers into the in-memory registry.

        Restart-safety (item #24): after a broker restart the registry is empty
        while per-org containers keep running. Adopting them restores the
        org→container mappings so the quota counts them and the reaper eventually
        reaps idle ones — closing the orphan-leak gap where a restart-orphaned
        container whose org never calls again would never be tracked or reaped.
        """
        if self._registry is None:
            return 0
        adopted = 0
        for org, container in self.list_sandbox_containers():
            if self._registry.get(org) is not None:
                continue
            info = self.to_info(org, container)
            if info.status == "running" and info.agent_url:
                self._registry.register(org, info.container_id, info.agent_url)
                adopted += 1
        return adopted

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
            # CHG-0143: not required (dev default) — NEVER raise, but make a degraded
            # (non-gVisor) posture LOUD instead of silent. The default host runtime (runc)
            # shares the kernel with untrusted tenant workloads; an operator who *thinks*
            # they deployed with gVisor but mis-set the env would otherwise get runc with
            # no signal (docs/mcp/BACKSTOP_FINDINGS.md: "silently degrading instead of
            # failing closed"). Warn ONCE (per manager) to avoid a line per create.
            if not runtime:
                if not self._runtime_degraded_warned:
                    logger.warning(
                        "MCP sandboxes are starting WITHOUT a kernel-isolation runtime: "
                        "MCP_SANDBOX_RUNTIME is unset, so Docker uses the default 'runc' "
                        "(shared host kernel). Set MCP_SANDBOX_RUNTIME=runsc and "
                        "MCP_SANDBOX_RUNTIME_REQUIRED=true for gVisor isolation in production."
                    )
                    self._runtime_degraded_warned = True
                return None
            if not self.runtime_available(runtime):
                if not self._runtime_degraded_warned:
                    logger.warning(
                        "Configured MCP_SANDBOX_RUNTIME=%r is NOT available in this Docker "
                        "daemon; FALLING BACK to the daemon default runtime (shared host "
                        "kernel, no gVisor isolation). Install the runtime, or set "
                        "MCP_SANDBOX_RUNTIME_REQUIRED=true to fail closed instead of "
                        "degrading.",
                        runtime,
                    )
                    self._runtime_degraded_warned = True
                # Return None, NOT the unavailable name. Docker does not fall back
                # on its own: handing it an unregistered runtime makes EVERY create
                # fail with 400 "unknown or invalid runtime name: runsc", which is
                # what took MCP tool execution down on the GCP aarch64 host (no
                # gVisor installed) — every tools/call 500'd while the warning above
                # claimed a fallback that never happened. Returning None uses the
                # daemon default, which is exactly what runtime_required=false means.
                #
                # The availability probe also has to run on EVERY call, not only on
                # the first: it previously sat inside the warn-once guard, so once
                # the warning had fired the check was skipped and the bad runtime
                # name was returned forever after.
                return None
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
        # CHG-0125: set BOTH upper- and lower-case proxy vars. curl honors ONLY the
        # lowercase ``http_proxy`` for plain HTTP (a deliberate curl behavior since the
        # httpoxy / CVE-2016-5385 era — its man page: "http_proxy is only used in
        # lowercase"); wget/git also prefer lowercase. With only the UPPERCASE vars set,
        # a sandboxed MCP server (or any subprocess) shelling out to curl/wget/git would
        # BYPASS the egress proxy and connect directly to arbitrary hosts even under
        # MCP_SANDBOX_EGRESS_LOCKDOWN=true — an exfiltration path that defeats the
        # egress-lockdown guardrail. Emitting both cases is the standard, defensive
        # convention (every production egress-proxy setup sets both).
        no_proxy = self.config.no_proxy
        return {
            "HTTP_PROXY": http_proxy,
            "HTTPS_PROXY": https_proxy,
            "NO_PROXY": no_proxy,
            "http_proxy": http_proxy,
            "https_proxy": https_proxy,
            "no_proxy": no_proxy,
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

    def _transport_stub_extra_hosts(self, org_slug: str) -> dict[str, str]:
        """Map *.stub aliases to live container IPs on the org sandbox network (runsc)."""
        try:
            net = self.client.networks.get(self.org_network_name(org_slug))
        except Exception:
            return {}
        containers = (net.attrs or {}).get("Containers") or {}
        name_to_ip: dict[str, str] = {}
        for ep in containers.values():
            ip = (ep.get("IPv4Address") or "").split("/")[0]
            name = ep.get("Name") or ""
            if ip and name:
                name_to_ip[name] = ip
        extra: dict[str, str] = {}
        for stub_host, candidates in _STUB_HOST_CANDIDATES:
            for cname in candidates:
                if cname in name_to_ip:
                    extra[stub_host] = name_to_ip[cname]
                    break
        return extra

    def _run_kwargs(self, org_slug: str) -> dict[str, Any]:
        runtime = self._resolve_runtime()
        org_net = self.ensure_org_network(org_slug)
        volume = self.volume_name(org_slug)
        mem_limit = f"{self.config.memory_mb}m"
        # Cap the Node/V8 old-space heap of spawned MCP servers BELOW the cgroup
        # mem_limit so a heavy Node server fails GRACEFULLY (catchable V8 "heap
        # out of memory") instead of the kernel SIGKILL-ing the whole sandbox
        # (exit -9 / 137). Default: ~75% of the container memory, min 256MB. This
        # is what turns Ruflo-class OOM crashes into a clean per-server error and
        # protects co-tenant servers in the shared per-org sandbox. (CP20)
        node_heap_mb = self.config.node_max_old_space_mb or max(
            256, int(self.config.memory_mb * 0.75)
        )
        volumes = {volume: {"bind": "/data/mcp-auth", "mode": "rw"}}
        self._assert_no_docker_socket_mount(volumes)
        environment = {
            "ORG_SLUG": org_slug,
            "MCP_REMOTE_CONFIG_DIR": "/data/mcp-auth",
            # Graceful-OOM heap cap for spawned Node MCP servers (CP20). Appended
            # (not overwritten) so any operator-supplied NODE_OPTIONS is preserved.
            "NODE_OPTIONS": (
                f"{os.environ.get('MCP_SANDBOX_NODE_OPTIONS', '').strip()} "
                f"--max-old-space-size={node_heap_mb}"
            ).strip(),
            "MCP_STDIO_MAX_PROCESSES_PER_ORG": os.environ.get(
                "MCP_STDIO_MAX_PROCESSES_PER_ORG", "16"
            ),
            # Sandbox USER is non-root; npm/uv caches need writable tmpfs (read_only rootfs).
            # npm cache MUST NOT live on noexec /tmp — npx bin symlinks are executed directly.
            "NPM_CONFIG_CACHE": "/var/npm-cache",
            # Kill postinstall lifecycle scripts during npx fetch (supply-chain RCE vector).
            "npm_config_ignore_scripts": "true",
            # BACKSTOP CHG-0022 (item 8): propagate the npm/PyPI package pin + allowlist
            # controls INTO the sandbox so the agent's stdio_manager can enforce them.
            # The enforcement code (sandbox-image/.../stdio_manager.py: _REQUIRE_PINNED_
            # PACKAGES / _PACKAGE_ALLOWLIST) reads these envs, but they were never
            # propagated by _run_kwargs — so they defaulted OFF (allow-any, no pin) and
            # only npm_config_ignore_scripts was active. Pass-through (default OFF) so an
            # operator opts in via the broker env WITHOUT breaking existing unpinned
            # servers (e.g. the "everything" test server is registered unpinned).
            "MCP_STDIO_REQUIRE_PINNED_PACKAGES": os.environ.get(
                "MCP_STDIO_REQUIRE_PINNED_PACKAGES", "false"
            ),
            "MCP_STDIO_PACKAGE_ALLOWLIST": os.environ.get(
                "MCP_STDIO_PACKAGE_ALLOWLIST", ""
            ),
            "UV_CACHE_DIR": "/var/cache/uv",
            "UV_PYTHON_INSTALL_DIR": "/var/cache/uv/python",
            # Writable HOME on tmpfs — host CLIs (semgrep, uv tool bins) write dotdirs here;
            # /home/sandbox is on the read-only rootfs (or absent under gVisor entrypoint).
            "HOME": "/var/cache/home",
            "XDG_CACHE_HOME": "/var/cache",
            # uvx installs tools to UV_TOOL_DIR (default ~/.local/share/uv/tools)
            # and links executables into UV_TOOL_BIN_DIR (default ~/.local/bin) —
            # BOTH on the read-only rootfs, so any uvx/Python MCP server (Fetch,
            # semgrep-mcp, …) failed to start ("Read-only file system"). Point them
            # at the writable /var/cache tmpfs so uvx servers can install+run. (CP36)
            "UV_TOOL_DIR": "/var/cache/uv/tools",
            "UV_TOOL_BIN_DIR": "/var/cache/uv/bin",
        }
        environment.update(self._egress_proxy_env())
        # CHG-0136: provision the OPT-IN broker→agent key into the sandbox so the agent
        # can verify the X-Sandbox-Agent-Key header the broker sends (a second
        # cross-tenant isolation layer, independent of network isolation). Same value the
        # broker uses in _post_agent_rpc. Unset ⇒ not provisioned ⇒ the agent doesn't
        # require it (backward-compatible). It is NOT in _SAFE_ENV_PASSTHROUGH and IS in
        # _SECRET_ENV_DENYLIST, so it never reaches a spawned MCP server's env.
        _agent_key = os.environ.get("MCP_AGENT_INTERNAL_KEY", "").strip()
        if _agent_key:
            environment["MCP_AGENT_INTERNAL_KEY"] = _agent_key
        if runtime == "runsc":
            dns = _gvisor_dns_servers()
            if dns:
                environment["MCP_SANDBOX_DNS"] = ",".join(dns)
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
            "read_only": runtime != "runsc",
            "user": "0" if runtime == "runsc" else self.config.sandbox_user,
            "security_opt": (
                [o for o in _security_opts() if o != "no-new-privileges:true"]
                if runtime == "runsc"
                else _security_opts()
            ),
            "cap_drop": ["ALL"],
            "cap_add": ["SETUID", "SETGID"] if runtime == "runsc" else [],
            "ulimits": _sandbox_ulimits(),
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=512m",
                # npm-cache is RAM-backed (counts against the mem cgroup). Heavy
                # servers (e.g. Ruflo, whose dep tree > 1GB) overflow the default
                # and fail with ENOSPC → raise MCP_SANDBOX_NPM_CACHE_SIZE_MB (and
                # usually MCP_SANDBOX_MEMORY_MB too, since tmpfs eats the cgroup). (CP21)
                "/var/npm-cache": f"rw,exec,nosuid,size={self.config.npm_cache_size_mb}m,mode=1777",
                "/var/cache": "rw,exec,nosuid,size=512m,mode=1777",
            },
        }
        # Host-run broker (no container ref) needs published agent port on shared bridge.
        if self._broker_container_ref() is None:
            kwargs["ports"] = {f"{self.config.agent_port}/tcp": None}
            kwargs["network"] = self.config.network
        if runtime:
            kwargs["runtime"] = runtime
            if runtime == "runsc":
                dns = _gvisor_dns_servers()
                if dns:
                    kwargs["dns"] = dns
                stub_hosts = self._transport_stub_extra_hosts(org_slug)
                if stub_hosts:
                    kwargs["extra_hosts"] = stub_hosts
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
        self._ensure_volume(org_slug)  # CHG-0113: labeled volume for tenant-verified destroy
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
            # CHG-0133: a container that won't start must be RECREATED — that is the whole
            # point of "or_recreate" (auto-recovery / chaos self-heal, item 18). The old
            # code recreated ONLY for a name-conflict or "marked for removal" error and
            # RE-RAISED every other start failure — so a sandbox left in a dead / OOM-killed
            # / corrupted state after a chaos kill (whose start() error is a generic OCI /
            # APIError, NOT a removal-race) was never recreated: the org's sandbox stayed
            # broken, failing every request until manual intervention. Now ANY start failure
            # triggers remove+recreate. Safe: the per-org volume persists (no data loss), and
            # a genuine daemon-down error still surfaces from create_container after its
            # retries (recreating can't make daemon-down worse — it fails there anyway).
            logger.warning(
                "sandbox start failed for %s (%s); removing + recreating", org_slug, exc
            )
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
            # CHG-0113: never destroy a volume LABELED for a DIFFERENT org (a legacy/
            # reused name colliding onto this org's canonical volume name). No label =
            # legacy unlabeled volume (its name is authoritative for canonical slugs
            # post-CHG-0111) → removable; a MISMATCHED org label → skip, fail-closed.
            _vorg = self._volume_labels(volume).get(LABEL_ORG_SLUG)
            if _vorg is not None and _vorg != org_slug:
                logger.warning(
                    "sandbox volume org-label mismatch: name=%s requested_org=%s "
                    "labeled_org=%s (refusing to remove, fail-closed)",
                    volume_name, org_slug, _vorg,
                )
            else:
                volume.remove(force=True)
                removed = True
        except Exception:
            pass
        if self._registry is not None:
            self._registry.remove(org_slug)
        return removed
