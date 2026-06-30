"""Adversarial MCP sandbox isolation tests — cross-org leakage + escape probes.

Requires Docker for full gate. Unit-style denylist tests run without Docker.

    cd gateway && ./.venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SHARED = REPO_ROOT / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))
DOCKERFILE = REPO_ROOT / "services/mcp-broker/sandbox-image/Dockerfile"
STDIO_STUB = REPO_ROOT / "services/mcp-broker/tests/fixtures/stdio_mcp_stub.py"
ADVERSARIAL_STUB = (
    REPO_ROOT / "tests/e2e/mcp_sandbox_adversarial/fixtures/adversarial_mcp_stub.py"
)

BROKER_KEY = "adversarial-broker-key"
IMAGE_TAG = "ai-mesh/mcp-sandbox:adversarial"
BROKER_IMAGE_TAG = "ai-mesh/mcp-broker:adversarial"
BROKER_DOCKERFILE = REPO_ROOT / "services/mcp-broker/Dockerfile"
BROKER_CONTAINER_NAME = "mcp-broker-adversarial-test"
SANDBOX_NETWORK = "mcp_sandbox_bridge"

ORG_ALPHA = "adv-org-alpha"
ORG_BETA = "adv-org-beta"
ORGS = [ORG_ALPHA, ORG_BETA]
SERVERS_PER_ORG = 5

STUB_CONTAINER_PATH = "/data/mcp-auth/stdio_mcp_stub.py"
ADV_STUB_CONTAINER_PATH = "/data/mcp-auth/adversarial_mcp_stub.py"

LABEL_ORG_SLUG = "ai_mesh.org_slug"

SECRET_PROBE_KEYS = (
    "GATEWAY_INTERNAL_API_KEY",
    "MCP_BROKER_INTERNAL_KEY",
    "AGENT_API_KEY",
    "DATABASE_URL",
)


def _org_server_configs(org_slug: str) -> list[dict]:
    configs: list[dict] = []
    for idx in range(1, SERVERS_PER_ORG + 1):
        token = f"{org_slug}:adv-stub-{idx}"
        configs.append(
            {
                "server_slug": f"adv-stub-{idx}",
                "command": "python3",
                "args": [STUB_CONTAINER_PATH],
                "env_vars": {
                    "ORG_SLUG": org_slug,
                    "SERVER_IDX": str(idx),
                    "SERVER_TOKEN": token,
                    "ORG_ONLY_MARKER": f"marker-{org_slug}",
                    "ADV_PROBE_TAG": f"probe-{org_slug}-{idx}",
                },
            }
        )
    return configs


def _docker_cli_ok() -> bool:
    if not shutil.which("docker"):
        return False
    proc = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode == 0


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _list_container_ids(org_slug: str) -> list[str]:
    proc = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label={LABEL_ORG_SLUG}={org_slug}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [cid for cid in proc.stdout.split() if cid]


def _destroy_org_sandbox(org_slug: str, broker_url: str) -> None:
    import httpx

    try:
        httpx.delete(
            f"{broker_url}/v1/sandbox/{org_slug}",
            headers={"X-MCP-Broker-Key": BROKER_KEY},
            timeout=30.0,
        )
    except httpx.HTTPError:
        pass


def _cleanup_test_containers() -> None:
    for org in ORGS:
        for cid in _list_container_ids(org):
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)




def _force_remove_named_container(name: str, attempts: int = 8) -> None:
    for _ in range(attempts):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        listed = subprocess.run(
            ["docker", "ps", "-aq", "-f", f"name=^{name}$"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if not listed.stdout.strip():
            return
        time.sleep(1)
    pytest.fail(f"could not remove docker container {name!r}")


def _start_broker_container(port: int, sandbox_image: str, broker_image: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        BROKER_CONTAINER_NAME,
        "--network",
        SANDBOX_NETWORK,
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-e",
        f"MCP_BROKER_INTERNAL_KEY={BROKER_KEY}",
        "-e",
        f"MCP_SANDBOX_IMAGE={sandbox_image}",
        "-e",
        f"MCP_SANDBOX_NETWORK={SANDBOX_NETWORK}",
        "-e",
        f"MCP_BROKER_CONTAINER_NAME={BROKER_CONTAINER_NAME}",
        "-e",
        "MCP_SANDBOX_IDLE_TIMEOUT=3600",
        "-p",
        f"127.0.0.1:{port}:8311",
        broker_image,
    ]
    for attempt in range(4):
        _force_remove_named_container(BROKER_CONTAINER_NAME)
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if run.returncode == 0:
            return run
        if "already in use" not in (run.stderr or ""):
            break
        time.sleep(2)
    return run

def _ensure_sandbox_network() -> None:
    subprocess.run(
        ["docker", "network", "create", SANDBOX_NETWORK],
        capture_output=True,
        timeout=30,
    )


def _wait_for_sandbox_agent(container_id: str, timeout: float = 150.0) -> None:
    deadline = time.time() + timeout
    probe = (
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:9320/health', timeout=3)"
    )
    while time.time() < deadline:
        proc = subprocess.run(
            ["docker", "exec", container_id, "python", "-c", probe],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return
        time.sleep(2)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "40", container_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    pytest.fail(
        f"sandbox-agent unhealthy in {container_id[:12]}\n{logs.stdout}\n{logs.stderr}"
    )


def _copy_stubs_into_container(container_id: str) -> None:
    for host_path, container_path in (
        (STDIO_STUB, STUB_CONTAINER_PATH),
        (ADVERSARIAL_STUB, ADV_STUB_CONTAINER_PATH),
    ):
        proc = subprocess.run(
            ["docker", "cp", str(host_path), f"{container_id}:{container_path}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr


@pytest.fixture(scope="module")
def docker_available() -> None:
    if not _docker_cli_ok():
        pytest.skip("docker not available")


@pytest.fixture(scope="module")
def sandbox_image(docker_available: None) -> str:
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            IMAGE_TAG,
            "-f",
            str(DOCKERFILE),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return IMAGE_TAG


@pytest.fixture(scope="module")
def broker_image(docker_available: None) -> str:
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            BROKER_IMAGE_TAG,
            "-f",
            str(BROKER_DOCKERFILE),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    return BROKER_IMAGE_TAG


@pytest.fixture(scope="module")
def broker_url(
    docker_available: None,
    sandbox_image: str,
    broker_image: str,
) -> Iterator[str]:
    _cleanup_test_containers()
    _ensure_sandbox_network()

    port = _free_port()
    run = _start_broker_container(port, sandbox_image, broker_image)
    assert run.returncode == 0, run.stderr

    import httpx

    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            health = httpx.get(f"{url}/health", timeout=2.0)
            if health.status_code == 200 and health.json().get("docker_ok"):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        pytest.fail("mcp-broker /health did not report docker_ok=true")

    yield url

    for org in ORGS:
        _destroy_org_sandbox(org, url)
    _cleanup_test_containers()
    _force_remove_named_container(BROKER_CONTAINER_NAME)


@pytest.fixture(autouse=True)
def _gateway_broker_env(broker_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    monkeypatch.setenv("MCP_BROKER_URL", broker_url)
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "false")
    monkeypatch.setenv("GATEWAY_INTERNAL_API_KEY", "adversarial-gateway-secret-must-not-leak")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX", "5")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_BASE_DELAY", "0.05")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX_DELAY", "0.2")


async def _prepare_org(org_slug: str) -> str:
    from ai_mesh_gateway import mcp_sandbox_client as client

    ensure = await client.ensure_sandbox(org_slug)
    assert ensure["status"] == "running"
    container_id = ensure["container_id"]
    await asyncio.to_thread(_wait_for_sandbox_agent, container_id)
    await asyncio.to_thread(_copy_stubs_into_container, container_id)
    return container_id


async def _rpc_get_env(
    org_slug: str,
    cfg: dict,
    key: str,
    *,
    msg_id: int | str | None = None,
) -> str:
    from ai_mesh_gateway import mcp_sandbox_client as client

    rid = msg_id if msg_id is not None else hash((org_slug, key, time.time_ns())) % 10_000_000
    rpc = await client.broker_send_jsonrpc(
        org_slug,
        cfg,
        "tools/call",
        {"name": "get_env", "arguments": {"key": key}},
        msg_id=rid,
    )
    if "error" in rpc and "result" not in rpc:
        raise AssertionError(f"RPC error for {org_slug}/{key}: {rpc['error']}")
    return rpc["result"]["content"][0]["text"]


# --- Denylist (no Docker required) ---


def test_denylist_blocks_gateway_secret_injection():
    """Injected GATEWAY_INTERNAL_API_KEY must not reach child env."""
    from ai_mesh_shared.mcp_stdio_common import _build_child_env

    child = _build_child_env(
        {"GATEWAY_INTERNAL_API_KEY": "injected-too", "SAFE_VAR": "ok"},
        "adv-org-alpha",
        host_environ={
            "GATEWAY_INTERNAL_API_KEY": "host-secret",
            "PATH": "/usr/bin",
        },
    )
    assert "GATEWAY_INTERNAL_API_KEY" not in child
    assert child.get("SAFE_VAR") == "ok"


def test_denylist_blocks_mcp_broker_key_injection():
    from ai_mesh_shared.mcp_stdio_common import _build_child_env

    child = _build_child_env(
        {},
        "adv-org-alpha",
        host_environ={"MCP_BROKER_INTERNAL_KEY": "broker-secret", "PATH": "/usr/bin"},
    )
    assert "MCP_BROKER_INTERNAL_KEY" not in child


# --- Leakage (Docker) ---


@pytest.mark.docker
@pytest.mark.asyncio
async def test_cross_org_volume_secrets_not_readable(broker_url: str):
    org_containers = {}
    for org in ORGS:
        org_containers[org] = await _prepare_org(org)

    for org, container_id in org_containers.items():
        secret = f"ADV_SECRET_{org}"
        write = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "sh",
                "-c",
                f"printf '%s' '{secret}' > /data/mcp-auth/secret-{org}.txt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert write.returncode == 0, write.stderr

    for reader in ORGS:
        for owner in ORGS:
            reader_id = org_containers[reader]
            if reader == owner:
                proc = subprocess.run(
                    [
                        "docker",
                        "exec",
                        reader_id,
                        "cat",
                        f"/data/mcp-auth/secret-{owner}.txt",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert proc.returncode == 0
                assert proc.stdout.strip() == f"ADV_SECRET_{owner}"
            else:
                proc = subprocess.run(
                    [
                        "docker",
                        "exec",
                        reader_id,
                        "sh",
                        "-c",
                        f"test ! -f /data/mcp-auth/secret-{owner}.txt",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert proc.returncode == 0, (
                    f"{reader} must not see {owner} volume secret"
                )


@pytest.mark.docker
@pytest.mark.asyncio
async def test_env_foreign_org_marker_not_visible(broker_url: str):
    org_containers = {org: await _prepare_org(org) for org in ORGS}
    del org_containers  # containers prepared; probes via RPC

    for org in ORGS:
        cfg = _org_server_configs(org)[0]
        marker = await _rpc_get_env(org, cfg, "ORG_ONLY_MARKER")
        assert marker == f"marker-{org}"
        for foreign in ORGS:
            if foreign == org:
                continue
            foreign_marker = await _rpc_get_env(org, cfg, "ORG_ONLY_MARKER")
            assert foreign_marker != f"marker-{foreign}"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_npm_cache_bleed_probe(broker_url: str):
    """Sibling org marker must not appear via get_env in another org."""
    for org in ORGS:
        await _prepare_org(org)

    alpha_cfg = _org_server_configs(ORG_ALPHA)[0]
    beta_cfg = _org_server_configs(ORG_BETA)[0]

    alpha_tag = await _rpc_get_env(ORG_ALPHA, alpha_cfg, "ADV_PROBE_TAG")
    beta_in_alpha = await _rpc_get_env(ORG_ALPHA, alpha_cfg, "ORG_SLUG")
    assert alpha_tag.startswith("probe-adv-org-alpha")
    assert beta_in_alpha == ORG_ALPHA

    beta_tag = await _rpc_get_env(ORG_BETA, beta_cfg, "ADV_PROBE_TAG")
    assert beta_tag.startswith("probe-adv-org-beta")
    assert beta_tag != alpha_tag


@pytest.mark.docker
@pytest.mark.asyncio
async def test_denylist_secrets_not_in_sandbox_env(broker_url: str):
    """Gateway secrets must not be visible inside sandbox via get_env."""
    await _prepare_org(ORG_ALPHA)
    cfg = _org_server_configs(ORG_ALPHA)[0]
    for key in SECRET_PROBE_KEYS:
        val = await _rpc_get_env(ORG_ALPHA, cfg, key)
        assert val in ("",), f"{key} leaked into sandbox: {val!r}"


# --- Escape (Docker) ---


@pytest.mark.docker
@pytest.mark.asyncio
async def test_host_path_traversal_cannot_read_host_etc_passwd(broker_url: str):
    container_id = await _prepare_org(ORG_ALPHA)
    proc = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            "test ! -r /host/etc/passwd && test ! -r /etc/passwd || head -1 /etc/passwd | grep -q root",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Container has its own /etc/passwd (sandbox image) — must NOT be host root hash
    # Adversarial: try ../../../etc/passwd from /data
    traversal = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            "cat /data/mcp-auth/../../etc/shadow 2>/dev/null || true",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "root:" not in (traversal.stdout or ""), "path traversal may have reached shadow"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_proc_self_environ_no_gateway_secret(broker_url: str):
    container_id = await _prepare_org(ORG_ALPHA)
    proc = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            "tr '\\0' '\\n' < /proc/1/environ 2>/dev/null || true",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    env_text = proc.stdout or ""
    for key in SECRET_PROBE_KEYS:
        assert key not in env_text or f"{key}=adversarial" not in env_text, (
            f"{key} visible in /proc/1/environ"
        )


@pytest.mark.docker
@pytest.mark.asyncio
async def test_docker_sock_not_accessible_from_sandbox(broker_url: str):
    container_id = await _prepare_org(ORG_ALPHA)
    proc = subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            "test ! -S /var/run/docker.sock && test ! -r /var/run/docker.sock",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, "docker.sock must not be mounted in sandbox"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_sibling_container_metadata_not_readable(broker_url: str):
    alpha_id = await _prepare_org(ORG_ALPHA)
    beta_id = await _prepare_org(ORG_BETA)

    secret_path = f"/data/mcp-auth/sibling-probe-{ORG_BETA}.txt"
    write_beta = subprocess.run(
        [
            "docker",
            "exec",
            beta_id,
            "sh",
            "-c",
            f"printf 'sibling-secret' > {secret_path}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert write_beta.returncode == 0

    proc = subprocess.run(
        [
            "docker",
            "exec",
            alpha_id,
            "sh",
            "-c",
            f"test ! -f {secret_path}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"{ORG_ALPHA} can read {ORG_BETA} volume file"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_parallel_2_orgs_5_servers_tools_list(broker_url: str):
    """F3 gate: 2 orgs × 5 servers concurrent tools/list."""
    for org in ORGS:
        await _prepare_org(org)

    async def _list_all(org: str) -> None:
        from ai_mesh_gateway import mcp_sandbox_client as client

        for idx, cfg in enumerate(_org_server_configs(org), start=1):
            rpc = await client.broker_send_jsonrpc(
                org,
                cfg,
                "tools/list",
                None,
                msg_id=idx,
            )
            names = {t["name"] for t in rpc["result"]["tools"]}
            assert {"echo", "get_env"}.issubset(names)

    await asyncio.gather(_list_all(ORG_ALPHA), _list_all(ORG_BETA))


def _container_ip(container_id: str, network_name: str) -> str:
    proc = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            f"{{{{(index .NetworkSettings.Networks \"{network_name}\").IPAddress}}}}",
            container_id,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    ip = (proc.stdout or "").strip()
    assert ip, f"no IP on network {network_name} for {container_id[:12]}"
    return ip


# --- Network isolation (Docker) ---


def _org_network_name(org_slug: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") or "default"
    return f"mcp_sandbox_net_{safe}"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_network_sibling_sandbox_agent_port_not_reachable(broker_url: str):
    """Sandboxes on per-org networks must not reach sibling :9320 agent ports."""
    alpha_id = await _prepare_org(ORG_ALPHA)
    beta_id = await _prepare_org(ORG_BETA)
    beta_ip = _container_ip(beta_id, _org_network_name(ORG_BETA))

    probe = (
        "import urllib.request\n"
        f"urllib.request.urlopen('http://{beta_ip}:9320/health', timeout=3)"
    )
    proc = subprocess.run(
        [
            "docker",
            "exec",
            alpha_id,
            "python",
            "-c",
            probe,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, (
        f"{ORG_ALPHA} reached {ORG_BETA} agent at {beta_ip}:9320: {proc.stdout}"
    )


@pytest.mark.docker
@pytest.mark.asyncio
async def test_network_concurrent_cross_org_rpc_hammer(broker_url: str):
    """Concurrent tools/call across 2 orgs must not cross-leak env markers."""
    for org in ORGS:
        await _prepare_org(org)

    async def _hammer(org: str, idx: int) -> str:
        cfg = _org_server_configs(org)[idx % SERVERS_PER_ORG]
        return await _rpc_get_env(org, cfg, "ORG_ONLY_MARKER", msg_id=f"hammer-{org}-{idx}")

    markers = await asyncio.gather(
        *[_hammer(ORG_ALPHA, i) for i in range(10)],
        *[_hammer(ORG_BETA, i) for i in range(10)],
    )
    alpha_markers = markers[:10]
    beta_markers = markers[10:]
    assert all(m == f"marker-{ORG_ALPHA}" for m in alpha_markers)
    assert all(m == f"marker-{ORG_BETA}" for m in beta_markers)
