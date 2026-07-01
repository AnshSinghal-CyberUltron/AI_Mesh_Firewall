"""Docker integration test — real mcp-broker + per-org sandbox containers (T1 gate).

Requires a local Docker daemon. Skipped when Docker is unavailable (CI agents without
socket access). Run:

    cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_integration.py -q
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "services/mcp-broker/sandbox-image/Dockerfile"
STDIO_STUB = REPO_ROOT / "services/mcp-broker/tests/fixtures/stdio_mcp_stub.py"

BROKER_KEY = "integration-test-broker-key"
IMAGE_TAG = "ai-mesh/mcp-sandbox:integration-test"
BROKER_IMAGE_TAG = "ai-mesh/mcp-broker:integration-test"
BROKER_DOCKERFILE = REPO_ROOT / "services/mcp-broker/Dockerfile"
BROKER_CONTAINER_NAME = "mcp-broker-integration-test"
SANDBOX_NETWORK = "mcp_sandbox_bridge"
TEST_PREFIX = "t1-integ"
ORG_ALPHA = f"{TEST_PREFIX}-alpha"
ORG_BETA = f"{TEST_PREFIX}-beta"

LABEL_ROLE = "ai_mesh.role"
LABEL_ORG_SLUG = "ai_mesh.org_slug"
ROLE_VALUE = "mcp-sandbox"

STUB_CONTAINER_PATH = "/data/mcp-auth/stdio_mcp_stub.py"

SERVER_CONFIG = {
    "server_slug": "stub-server",
    "command": "python3",
    "args": [STUB_CONTAINER_PATH],
    "env_vars": {"INTEGRATION_MARKER": "org-visible"},
}


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
    for org in (ORG_ALPHA, ORG_BETA):
        for cid in _list_container_ids(org):
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)


def _inspect_container(org_slug: str) -> dict:
    import json

    name = f"mcp-sandbox-{org_slug}"
    proc = subprocess.run(
        ["docker", "inspect", name, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.fixture(scope="module")
def docker_available() -> None:
    if not _docker_cli_ok():
        pytest.skip("docker not available")


@pytest.fixture(scope="module")
def sandbox_image(docker_available: None) -> str:
    """Build the sandbox image once for this module."""
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


def _ensure_sandbox_network() -> None:
    subprocess.run(
        ["docker", "network", "create", SANDBOX_NETWORK],
        capture_output=True,
        timeout=30,
    )


@pytest.fixture(scope="module")
def broker_url(
    docker_available: None,
    sandbox_image: str,
    broker_image: str,
) -> Iterator[str]:
    """Run mcp-broker in Docker on the sandbox bridge (matches compose topology)."""
    _cleanup_test_containers()
    subprocess.run(
        ["docker", "rm", "-f", BROKER_CONTAINER_NAME],
        capture_output=True,
        timeout=30,
    )
    _ensure_sandbox_network()

    port = _free_port()
    run = subprocess.run(
        [
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
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
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
        logs = subprocess.run(
            ["docker", "logs", BROKER_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
        pytest.fail(
            "mcp-broker /health did not report docker_ok=true\n"
            f"{logs.stdout}\n{logs.stderr}"
        )

    yield url

    _destroy_org_sandbox(ORG_ALPHA, url)
    _destroy_org_sandbox(ORG_BETA, url)
    _cleanup_test_containers()
    subprocess.run(
        ["docker", "rm", "-f", BROKER_CONTAINER_NAME],
        capture_output=True,
        timeout=60,
    )


@pytest.fixture(autouse=True)
def _gateway_broker_env(broker_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", BROKER_KEY)
    monkeypatch.setenv("MCP_BROKER_URL", broker_url)
    monkeypatch.setenv("MCP_STDIO_IN_PROCESS", "false")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX", "5")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_BASE_DELAY", "0.05")
    monkeypatch.setenv("MCP_SANDBOX_RETRY_MAX_DELAY", "0.2")


def _wait_for_sandbox_agent(container_id: str, timeout: float = 150.0) -> None:
    """Wait until sandbox-agent /health responds inside the container."""
    deadline = time.time() + timeout
    probe = (
        "import urllib.request; "
        "urllib.request.urlopen('http://127.0.0.1:9320/health', timeout=3)"
    )
    while time.time() < deadline:
        state = subprocess.run(
            ["docker", "inspect", container_id, "--format", "{{.State.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if state.stdout.strip() not in ("running", ""):
            break
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
        f"sandbox-agent did not become healthy in container {container_id[:12]}\n"
        f"{logs.stdout}\n{logs.stderr}"
    )


async def _prepare_org_sandbox(org_slug: str) -> str:
    """Ensure org sandbox, wait for agent, seed stdio stub; return container_id."""
    from ai_mesh_gateway import mcp_sandbox_client as client

    ensure = await client.ensure_sandbox(org_slug)
    assert ensure["status"] == "running"
    container_id = ensure["container_id"]
    _wait_for_sandbox_agent(container_id)
    _copy_stub_into_container(container_id)
    return container_id


def _copy_stub_into_container(container_id: str) -> None:
    proc = subprocess.run(
        ["docker", "cp", str(STDIO_STUB), f"{container_id}:{STUB_CONTAINER_PATH}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def _container_for_org(org_slug: str) -> dict:
    return _inspect_container(org_slug)


@pytest.mark.docker
@pytest.mark.asyncio
async def test_ensure_sandbox_labels_and_broker_rpc(broker_url: str):
    from ai_mesh_gateway import mcp_sandbox_client as client

    container_id = await _prepare_org_sandbox(ORG_ALPHA)

    container = _container_for_org(ORG_ALPHA)
    labels = container.get("Config", {}).get("Labels", {})
    assert labels.get(LABEL_ROLE) == ROLE_VALUE
    assert labels.get(LABEL_ORG_SLUG) == ORG_ALPHA
    assert container_id

    rpc = await client.broker_send_jsonrpc(
        ORG_ALPHA,
        SERVER_CONFIG,
        "tools/list",
        None,
        msg_id=11,
    )
    assert rpc["id"] == 11
    tools = rpc["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"echo", "get_env"}.issubset(names)


@pytest.mark.docker
@pytest.mark.asyncio
async def test_send_jsonrpc_full_path_via_adapter(broker_url: str):
    import sys
    from pathlib import Path

    gw = Path(__file__).resolve().parents[1]
    shared = gw.parent.parent / "shared"
    if str(gw) not in sys.path:
        sys.path.insert(0, str(gw))
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.append(str(shared))

    from ai_mesh_gateway import mcp_sandbox_client as client
    from mcp_stdio_adapter import send_jsonrpc

    await _prepare_org_sandbox(ORG_ALPHA)

    result = await send_jsonrpc(
        org_slug=ORG_ALPHA,
        server_slug=SERVER_CONFIG["server_slug"],
        command=SERVER_CONFIG["command"],
        args=SERVER_CONFIG["args"],
        env=SERVER_CONFIG["env_vars"],
        method="tools/call",
        params={"name": "echo", "arguments": {"msg": "sandbox-ok"}},
        msg_id=21,
        server_config={**SERVER_CONFIG, "org_slug": ORG_ALPHA},
    )
    assert result["id"] == 21
    assert result["result"]["content"][0]["text"] == "sandbox-ok"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_cross_org_volume_and_env_isolation(broker_url: str):
    from ai_mesh_gateway import mcp_sandbox_client as client

    # Prepare beta first so both sandboxes are warm before isolation assertions.
    beta_id = await _prepare_org_sandbox(ORG_BETA)
    alpha_id = await _prepare_org_sandbox(ORG_ALPHA)

    secret = "ORG_ALPHA_ONLY_SECRET"
    write = subprocess.run(
        [
            "docker",
            "exec",
            alpha_id,
            "sh",
            "-c",
            f"printf '%s' '{secret}' > /data/mcp-auth/leak-marker.txt",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert write.returncode == 0, write.stderr

    read_beta = subprocess.run(
        [
            "docker",
            "exec",
            beta_id,
            "sh",
            "-c",
            "test ! -f /data/mcp-auth/leak-marker.txt",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert read_beta.returncode == 0, "org-beta must not see org-alpha auth volume files"

    alpha_env = await client.broker_send_jsonrpc(
        ORG_ALPHA,
        {**SERVER_CONFIG, "env_vars": {"CROSS_ORG_PROBE": "alpha-value"}},
        "tools/call",
        {"name": "get_env", "arguments": {"key": "CROSS_ORG_PROBE"}},
        msg_id=31,
    )
    assert alpha_env["result"]["content"][0]["text"] == "alpha-value"

    beta_env = await client.broker_send_jsonrpc(
        ORG_BETA,
        SERVER_CONFIG,
        "tools/call",
        {"name": "get_env", "arguments": {"key": "CROSS_ORG_PROBE"}},
        msg_id=32,
    )
    assert beta_env["result"]["content"][0]["text"] == ""
