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
    SandboxRuntimeUnavailableError,
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
    container.name = f"{org_slug}-mcp-sandbox"
    container.status = status
    # A real sandbox container ALWAYS carries these labels (set by ensure via
    # manager.labels(org_slug)); CHG-0112 verifies them on by-name lookup.
    container.labels = {LABEL_ROLE: ROLE_VALUE, LABEL_ORG_SLUG: org_slug}
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
    client.info.return_value = {"Runtimes": {"runc": {}, "runsc": {}}}
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
    assert run_kwargs["read_only"] is True
    assert run_kwargs["init"] is True
    assert run_kwargs["user"] == "sandbox"
    assert run_kwargs["security_opt"] == ["no-new-privileges:true"]
    assert run_kwargs["cap_drop"] == ["ALL"]
    assert run_kwargs["memswap_limit"] == "1024m"
    # Only nofile — nproc is intentionally NOT set: RLIMIT_NPROC is per host-UID,
    # and all org sandboxes share uid 1000(sandbox), so an nproc ulimit is a
    # cross-tenant shared cap that breaks multi-org scaling. pids_limit (per
    # container) provides fork containment instead.
    assert len(run_kwargs["ulimits"]) == 1
    _ulimit_names = [
        (u.get("Name") if isinstance(u, dict) else getattr(u, "name", None))
        for u in run_kwargs["ulimits"]
    ]
    assert "nofile" in _ulimit_names
    assert "nproc" not in _ulimit_names
    assert run_kwargs["environment"]["ORG_SLUG"] == "acme"
    assert run_kwargs["environment"]["NPM_CONFIG_CACHE"] == "/var/npm-cache"
    assert run_kwargs["environment"]["npm_config_ignore_scripts"] == "true"
    assert "/var/npm-cache" in run_kwargs["tmpfs"]
    assert "noexec" in run_kwargs["tmpfs"]["/tmp"]
    assert "exec" in run_kwargs["tmpfs"]["/var/npm-cache"]
    assert "noexec" not in run_kwargs["tmpfs"]["/var/npm-cache"]
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


def test_ensure_recovers_from_name_conflict(manager: DockerManager):
    """When create hits 409, fall back to get-by-name instead of 500."""
    existing = _mock_container(org_slug="acme")
    manager.client.containers.list.return_value = []
    manager.client.containers.get.side_effect = None
    manager.client.containers.get.return_value = existing
    conflict = Exception("name already in use")
    conflict.status_code = 409  # type: ignore[attr-defined]
    manager.client.containers.run.side_effect = conflict

    info = manager.ensure("acme")

    manager.client.containers.get.assert_called_with("acme-mcp-sandbox")
    assert info.status == "running"
    assert info.container_id == existing.id


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
    monkeypatch.setenv("MCP_SANDBOX_RUNTIME_REQUIRED", "true")
    monkeypatch.setenv("MCP_SANDBOX_EGRESS_LOCKDOWN", "true")
    monkeypatch.setenv("MCP_SANDBOX_HTTP_PROXY", "http://proxy.test:3128")

    config = SandboxDockerConfig.from_env()

    assert config.image == "custom/sandbox:v2"
    assert config.memory_mb == 4096
    assert config.cpus == 2.5
    assert config.runtime == "runsc"
    assert config.runtime_required is True
    assert config.egress_lockdown is True
    assert config.http_proxy == "http://proxy.test:3128"


def test_run_kwargs_set_node_heap_cap_derived_from_memory(manager: DockerManager):
    """CP20: NODE_OPTIONS caps the Node/V8 old-space heap BELOW the cgroup
    mem_limit (default ~75% of memory_mb) so a heavy Node MCP server fails with a
    graceful V8 OOM instead of a kernel SIGKILL that takes down the sandbox."""
    run_kwargs = manager._run_kwargs("acme")
    node_options = run_kwargs["environment"]["NODE_OPTIONS"]
    # memory_mb=1024 → 75% = 768, strictly below the 1024m mem_limit.
    assert "--max-old-space-size=768" in node_options
    assert run_kwargs["mem_limit"] == "1024m"


def test_run_kwargs_node_heap_cap_explicit_and_preserves_operator_options(monkeypatch: pytest.MonkeyPatch):
    """CP20: an explicit heap cap wins, and any operator NODE_OPTIONS is preserved
    (appended, not overwritten)."""
    monkeypatch.setenv("MCP_SANDBOX_NODE_OPTIONS", "--dns-result-order=ipv4first")
    config = SandboxDockerConfig(
        image="ai-mesh/mcp-sandbox:test", memory_mb=2048, node_max_old_space_mb=1536,
    )
    manager = DockerManager(client=_mock_client(), config=config)
    node_options = manager._run_kwargs("acme")["environment"]["NODE_OPTIONS"]
    assert "--dns-result-order=ipv4first" in node_options
    assert "--max-old-space-size=1536" in node_options


def test_config_from_env_node_max_old_space(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_NODE_MAX_OLD_SPACE_MB", "3072")
    assert SandboxDockerConfig.from_env().node_max_old_space_mb == 3072


def test_run_kwargs_npm_cache_tmpfs_default_and_configurable():
    """CP21: the RAM-backed npm-cache tmpfs size is configurable so heavy servers
    (Ruflo) with large dependency trees don't fail with ENOSPC at the 1GB default."""
    m = DockerManager(client=_mock_client(), config=SandboxDockerConfig(image="x"))
    assert "size=1024m" in m._run_kwargs("acme")["tmpfs"]["/var/npm-cache"]
    big = DockerManager(client=_mock_client(), config=SandboxDockerConfig(image="x", npm_cache_size_mb=3072))
    assert "size=3072m" in big._run_kwargs("acme")["tmpfs"]["/var/npm-cache"]


def test_config_from_env_npm_cache_size(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_NPM_CACHE_SIZE_MB", "4096")
    assert SandboxDockerConfig.from_env().npm_cache_size_mb == 4096


def test_run_kwargs_include_runtime_when_configured():
    config = SandboxDockerConfig(
        image="ai-mesh/mcp-sandbox:test",
        network="mcp_sandbox_bridge",
        runtime="runsc",
    )
    manager = DockerManager(client=_mock_client(), config=config)
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")

    manager.ensure("acme")

    run_kwargs = manager.client.containers.run.call_args.kwargs
    assert run_kwargs["runtime"] == "runsc"


def test_runtime_required_fails_when_unset():
    config = SandboxDockerConfig(runtime_required=True)
    manager = DockerManager(client=_mock_client(), config=config)

    with pytest.raises(SandboxRuntimeUnavailableError, match="unset"):
        manager._run_kwargs("acme")


def test_runtime_required_fails_when_unavailable():
    client = _mock_client()
    client.info.return_value = {"Runtimes": {"runc": {}}}
    config = SandboxDockerConfig(runtime="runsc", runtime_required=True)
    manager = DockerManager(client=client, config=config)

    with pytest.raises(SandboxRuntimeUnavailableError, match="not available"):
        manager._run_kwargs("acme")


def test_runtime_required_succeeds_when_runsc_available():
    config = SandboxDockerConfig(runtime="runsc", runtime_required=True)
    manager = DockerManager(client=_mock_client(), config=config)
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")

    manager.ensure("acme")

    run_kwargs = manager.client.containers.run.call_args.kwargs
    assert run_kwargs["runtime"] == "runsc"


def test_run_kwargs_propagate_npm_supply_chain_controls(monkeypatch: pytest.MonkeyPatch):
    """BACKSTOP CHG-0022 (item 8): the npm/PyPI pin + allowlist controls are propagated
    INTO the sandbox env (default OFF pass-through) so the agent's stdio_manager can
    enforce them. Previously only npm_config_ignore_scripts was set, leaving the pin/
    allowlist checks unreachable (default allow-any / no-pin)."""
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    # Default: propagated with safe OFF defaults (existing unpinned servers still run).
    monkeypatch.delenv("MCP_STDIO_REQUIRE_PINNED_PACKAGES", raising=False)
    monkeypatch.delenv("MCP_STDIO_PACKAGE_ALLOWLIST", raising=False)
    env = manager._run_kwargs("acme")["environment"]
    assert env["MCP_STDIO_REQUIRE_PINNED_PACKAGES"] == "false"
    assert env["MCP_STDIO_PACKAGE_ALLOWLIST"] == ""
    # When an operator sets them on the broker, they now REACH the sandbox.
    monkeypatch.setenv("MCP_STDIO_REQUIRE_PINNED_PACKAGES", "true")
    monkeypatch.setenv("MCP_STDIO_PACKAGE_ALLOWLIST", "@scope/pkg,@other/pkg")
    env2 = manager._run_kwargs("acme")["environment"]
    assert env2["MCP_STDIO_REQUIRE_PINNED_PACKAGES"] == "true"
    assert env2["MCP_STDIO_PACKAGE_ALLOWLIST"] == "@scope/pkg,@other/pkg"


def test_egress_lockdown_injects_proxy_env():
    config = SandboxDockerConfig(
        egress_lockdown=True,
        http_proxy="http://allowlist-proxy:3128",
    )
    manager = DockerManager(client=_mock_client(), config=config)
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")

    manager.ensure("acme")

    env = manager.client.containers.run.call_args.kwargs["environment"]
    assert env["HTTP_PROXY"] == "http://allowlist-proxy:3128"
    assert env["HTTPS_PROXY"] == "http://allowlist-proxy:3128"
    assert "NO_PROXY" in env
    # CHG-0125: BOTH cases must be set — curl/wget/git honor ONLY the lowercase
    # variants, so uppercase-only leaves an egress-proxy BYPASS (direct exfil path).
    assert env["http_proxy"] == "http://allowlist-proxy:3128"
    assert env["https_proxy"] == "http://allowlist-proxy:3128"
    assert env["no_proxy"] == env["NO_PROXY"]


def test_no_egress_proxy_env_when_lockdown_off_and_no_proxy():
    # Default config: lockdown off, no explicit proxy -> NO proxy vars injected at all
    # (neither case), so the absence is unambiguous and can't be half-set.
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    env = manager._run_kwargs("acme")["environment"]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
              "http_proxy", "https_proxy", "no_proxy"):
        assert k not in env


def test_refuses_docker_socket_mount():
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())

    with pytest.raises(ValueError, match="docker.sock"):
        manager._assert_no_docker_socket_mount({"/var/run/docker.sock": {"bind": "/sock", "mode": "rw"}})


def test_security_opts_includes_seccomp_when_profile_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_SANDBOX_SECCOMP_PROFILE", "/etc/docker/seccomp.json")
    from sandbox.docker_manager import _security_opts

    assert _security_opts() == [
        "no-new-privileges:true",
        "seccomp=/etc/docker/seccomp.json",
    ]


# P7.25 — Per-org credential/env isolation: Docker resource naming invariants


def test_per_org_network_names_are_distinct():
    # Org A and Org B must get DIFFERENT network names — sharing a network
    # allows containers to reach sibling agent ports (isolation failure).
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    orgs = ["alpha", "beta", "gamma-corp", "zeroshield"]
    names = [manager.org_network_name(org) for org in orgs]
    assert len(set(names)) == len(orgs), "Per-org network names must be unique"
    for org, name in zip(orgs, names):
        assert org.replace("-", "-") in name or org.replace("-", "_") in name or org in name


def test_per_org_volume_names_are_distinct():
    # Each org's auth volume must have a unique name to prevent credential
    # cross-contamination via the /data/mcp-auth mount point.
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    orgs = ["alpha", "beta", "gamma-corp", "zeroshield"]
    vol_names = [manager.volume_name(org) for org in orgs]
    assert len(set(vol_names)) == len(orgs), "Per-org volume names must be unique"


def test_per_org_container_names_are_distinct():
    # Each org gets a different container name; a naming collision would cause
    # Docker to refuse the create (name-in-use 409) — verify the naming formula
    # produces unique identifiers.
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    orgs = ["alpha", "beta", "gamma-corp", "zeroshield"]
    container_names = [manager.container_name(org) for org in orgs]
    assert len(set(container_names)) == len(orgs), "Per-org container names must be unique"


def test_org_slug_in_sandbox_environment():
    # The sandbox container must receive ORG_SLUG in its env so the in-container
    # agent can enforce per-org resource boundaries (e.g. process caps).
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")

    manager.ensure("acme")

    env = manager.client.containers.run.call_args.kwargs["environment"]
    assert env.get("ORG_SLUG") == "acme"


def test_auth_volume_is_org_specific_for_credential_isolation():
    # OAuth tokens are stored on a per-org named volume (mcp_sandbox_{org}_auth)
    # mounted at /data/mcp-auth.  Two orgs must use DIFFERENT volume names even
    # though the mount path is the same — Docker volume isolation ensures Org A
    # cannot read Org B's credential store.
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    manager.client.containers.run.return_value = _mock_container(org_slug="acme")
    manager.ensure("acme")
    volumes_acme = manager.client.containers.run.call_args.kwargs["volumes"]

    manager.client.containers.run.reset_mock()
    manager.client.containers.run.return_value = _mock_container(org_slug="beta")
    manager.ensure("beta")
    volumes_beta = manager.client.containers.run.call_args.kwargs["volumes"]

    # Different volume names → different filesystems (Docker isolation)
    vol_name_acme = list(volumes_acme.keys())[0]
    vol_name_beta = list(volumes_beta.keys())[0]
    assert vol_name_acme != vol_name_beta
    # Both mount to the same well-known path inside each container
    assert volumes_acme[vol_name_acme]["bind"] == "/data/mcp-auth"
    assert volumes_beta[vol_name_beta]["bind"] == "/data/mcp-auth"


def test_sandbox_labels_include_org_slug_for_isolation():
    # Sandbox containers are labeled with their org_slug so the reconciler and
    # reaper can identify which org a container belongs to — without this, orphan
    # re-adoption is impossible after a broker restart.
    manager = DockerManager(client=_mock_client(), config=SandboxDockerConfig())
    labels = manager.labels("zeroshield")
    assert labels[LABEL_ORG_SLUG] == "zeroshield"
    assert labels[LABEL_ROLE] == ROLE_VALUE


# ── CHG-0112: by-name container lookup must VERIFY the org label (defense-in-depth
# completion of CHG-0111). A container that owns the deterministic NAME but is labeled
# for a DIFFERENT org (a pre-CHG-0111 collided container, or a renamed/reused name)
# must NOT be returned for this org — the org LABEL is the authoritative tenant key.


def test_get_container_by_name_returns_when_org_label_matches(manager: DockerManager):
    c = _mock_container(org_slug="acme")
    manager.client.containers.get.side_effect = None
    manager.client.containers.get.return_value = c
    assert manager.get_container_by_name("acme") is c


def test_get_container_by_name_rejects_mismatched_org_label(manager: DockerManager):
    foreign = _mock_container(org_slug="acme")  # owns the "acme-mcp-sandbox" name…
    foreign.labels = {LABEL_ROLE: ROLE_VALUE, LABEL_ORG_SLUG: "other-tenant"}  # …but is org B's
    manager.client.containers.get.side_effect = None
    manager.client.containers.get.return_value = foreign
    assert manager.get_container_by_name("acme") is None  # fail-closed, no cross-tenant


def test_get_container_by_name_rejects_missing_or_wrong_role(manager: DockerManager):
    c = _mock_container(org_slug="acme")
    c.labels = {LABEL_ORG_SLUG: "acme"}  # no role label → not a sandbox container
    manager.client.containers.get.side_effect = None
    manager.client.containers.get.return_value = c
    assert manager.get_container_by_name("acme") is None


def test_find_container_ignores_name_match_with_foreign_label(manager: DockerManager):
    # by-name returns a FOREIGN-labeled container; find_container must ignore it and
    # use the authoritative label filter, which returns the CORRECT org's container.
    foreign = _mock_container(org_slug="acme", container_id="foreign-id")
    foreign.labels = {LABEL_ROLE: ROLE_VALUE, LABEL_ORG_SLUG: "other-tenant"}
    correct = _mock_container(org_slug="acme", container_id="correct-id")
    manager.client.containers.get.side_effect = None
    manager.client.containers.get.return_value = foreign
    manager.client.containers.list.return_value = [correct]
    got = manager.find_container("acme")
    assert got is correct and got.id == "correct-id"


# ── CHG-0113: the sandbox VOLUME (auth store at /data/mcp-auth) is now created WITH the
# org label and its label is VERIFIED on destroy — completing CHG-0112's "label is the
# authoritative tenant key" for the volume resource. A volume labeled for a DIFFERENT org
# (a legacy/reused name colliding onto this org's canonical volume name) must NOT be
# destroyed for this org (cross-tenant data-destruction, fail-closed).


def _mock_volume(*, org_slug=None):
    v = MagicMock()
    v.attrs = {
        "Labels": ({LABEL_ROLE: ROLE_VALUE, LABEL_ORG_SLUG: org_slug} if org_slug else None)
    }
    return v


def test_ensure_volume_creates_labeled_when_missing(manager: DockerManager):
    manager.client.volumes.get.side_effect = Exception("not found")
    manager._ensure_volume("acme")
    manager.client.volumes.create.assert_called_once()
    kwargs = manager.client.volumes.create.call_args.kwargs
    assert kwargs["labels"][LABEL_ORG_SLUG] == "acme"
    assert kwargs["labels"][LABEL_ROLE] == ROLE_VALUE


def test_ensure_volume_idempotent_when_exists(manager: DockerManager):
    manager.client.volumes.get.side_effect = None
    manager.client.volumes.get.return_value = _mock_volume(org_slug="acme")
    manager._ensure_volume("acme")
    manager.client.volumes.create.assert_not_called()


def test_destroy_removes_unlabeled_legacy_volume(manager: DockerManager):
    vol = _mock_volume(org_slug=None)  # legacy auto-created, no labels
    manager.client.volumes.get.side_effect = None
    manager.client.volumes.get.return_value = vol
    manager.destroy("acme")
    vol.remove.assert_called_once_with(force=True)


def test_destroy_removes_same_org_volume(manager: DockerManager):
    vol = _mock_volume(org_slug="acme")
    manager.client.volumes.get.side_effect = None
    manager.client.volumes.get.return_value = vol
    manager.destroy("acme")
    vol.remove.assert_called_once_with(force=True)


def test_destroy_refuses_foreign_labeled_volume(manager: DockerManager):
    vol = _mock_volume(org_slug="other-tenant")  # owns the name, but is org B's data
    manager.client.volumes.get.side_effect = None
    manager.client.volumes.get.return_value = vol
    manager.destroy("acme")
    vol.remove.assert_not_called()  # fail-closed: no cross-tenant volume destruction


# ── CHG-0133: _start_or_recreate must recreate a container that won't start on ANY
# start failure (dead/OOM-killed/corrupted after a chaos kill), not only on a
# name-conflict / "marked for removal" removal-race. Auto-recovery / chaos self-heal.

def test_ensure_recreates_container_on_generic_start_failure(manager):
    dead = _mock_container(status="exited", container_id="dead1")
    dead.start.side_effect = RuntimeError("OCI runtime error: container is dead")
    fresh = _mock_container(status="running", container_id="fresh1")
    fresh.reload.side_effect = lambda: None
    manager.find_container = MagicMock(return_value=dead)
    manager.client.containers.run.return_value = fresh

    info = manager.ensure("acme")

    dead.start.assert_called_once()                    # attempted start
    dead.remove.assert_called_once_with(force=True)    # CHG-0133: removed the broken one
    manager.client.containers.run.assert_called_once()  # recreated
    assert info.container_id == "fresh1"
    assert info.status == "running"


def test_ensure_does_not_recreate_when_start_succeeds(manager):
    stopped = _mock_container(status="exited", container_id="keep1")
    stopped.start.side_effect = None  # start succeeds
    stopped.reload.side_effect = lambda: None
    manager.find_container = MagicMock(return_value=stopped)

    info = manager.ensure("acme")

    stopped.start.assert_called_once()
    stopped.remove.assert_not_called()                 # NOT recreated on a good start
    manager.client.containers.run.assert_not_called()
    assert info.container_id == "keep1"
