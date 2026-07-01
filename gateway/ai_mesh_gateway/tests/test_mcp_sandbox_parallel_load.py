"""E1 parallel-load gate — 5 orgs × 5 stdio MCP servers, concurrent RPC, no leakage.

Requires a local Docker daemon. Skipped when Docker is unavailable.

    cd gateway && ./.venv/bin/python -m pytest \
        ai_mesh_gateway/tests/test_mcp_sandbox_parallel_load.py -q
"""

from __future__ import annotations

import asyncio
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

BROKER_KEY = "parallel-load-broker-key"
IMAGE_TAG = "ai-mesh/mcp-sandbox:parallel-load"
BROKER_IMAGE_TAG = "ai-mesh/mcp-broker:parallel-load"
BROKER_DOCKERFILE = REPO_ROOT / "services/mcp-broker/Dockerfile"
BROKER_CONTAINER_NAME = "mcp-broker-parallel-load-test"
SANDBOX_NETWORK = "mcp_sandbox_bridge"

ORGS = [f"sandbox-org-{i}" for i in range(1, 6)]
SERVERS_PER_ORG = 5
STUB_CONTAINER_PATH = "/data/mcp-auth/stdio_mcp_stub.py"

LABEL_ORG_SLUG = "ai_mesh.org_slug"


def _org_server_configs(org_slug: str) -> list[dict]:
    """Five distinct stdio endpoints per org — same stub, unique env per server."""
    configs: list[dict] = []
    for idx in range(1, SERVERS_PER_ORG + 1):
        token = f"{org_slug}:stub-{idx}"
        configs.append(
            {
                "server_slug": f"stub-{idx}",
                "command": "python3",
                "args": [STUB_CONTAINER_PATH],
                "env_vars": {
                    "ORG_SLUG": org_slug,
                    "SERVER_IDX": str(idx),
                    "SERVER_TOKEN": token,
                    "ORG_ONLY_MARKER": f"marker-{org_slug}",
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


def _copy_stub_into_container(container_id: str) -> None:
    proc = subprocess.run(
        ["docker", "cp", str(STDIO_STUB), f"{container_id}:{STUB_CONTAINER_PATH}"],
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

    for org in ORGS:
        _destroy_org_sandbox(org, url)
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


async def _prepare_org_sandbox(org_slug: str) -> str:
    from ai_mesh_gateway import mcp_sandbox_client as client

    ensure = await client.ensure_sandbox(org_slug)
    assert ensure["status"] == "running"
    container_id = ensure["container_id"]
    await asyncio.to_thread(_wait_for_sandbox_agent, container_id)
    await asyncio.to_thread(_copy_stub_into_container, container_id)
    return container_id


async def _prepare_all_orgs() -> dict[str, str]:
    results = await asyncio.gather(*[_prepare_org_sandbox(org) for org in ORGS])
    return dict(zip(ORGS, results, strict=True))


def _msg_id(org_slug: str, server_idx: int, op: int) -> int:
    org_num = int(org_slug.rsplit("-", 1)[-1])
    return org_num * 1000 + server_idx * 10 + op


async def _rpc_tools_list(org_slug: str, cfg: dict, server_idx: int) -> None:
    from ai_mesh_gateway import mcp_sandbox_client as client

    rpc = await client.broker_send_jsonrpc(
        org_slug,
        cfg,
        "tools/list",
        None,
        msg_id=_msg_id(org_slug, server_idx, 1),
    )
    assert rpc["id"] == _msg_id(org_slug, server_idx, 1)
    names = {t["name"] for t in rpc["result"]["tools"]}
    assert {"echo", "get_env"}.issubset(names)


async def _rpc_tools_call_echo(org_slug: str, cfg: dict, server_idx: int) -> None:
    from ai_mesh_gateway import mcp_sandbox_client as client

    token = cfg["env_vars"]["SERVER_TOKEN"]
    rpc = await client.broker_send_jsonrpc(
        org_slug,
        cfg,
        "tools/call",
        {"name": "echo", "arguments": {"msg": token}},
        msg_id=_msg_id(org_slug, server_idx, 2),
    )
    assert rpc["id"] == _msg_id(org_slug, server_idx, 2)
    assert rpc["result"]["content"][0]["text"] == token


async def _rpc_tools_call_org_slug(org_slug: str, cfg: dict, server_idx: int) -> None:
    from ai_mesh_gateway import mcp_sandbox_client as client

    rpc = await client.broker_send_jsonrpc(
        org_slug,
        cfg,
        "tools/call",
        {"name": "get_env", "arguments": {"key": "ORG_SLUG"}},
        msg_id=_msg_id(org_slug, server_idx, 3),
    )
    assert rpc["id"] == _msg_id(org_slug, server_idx, 3)
    assert rpc["result"]["content"][0]["text"] == org_slug


async def _rpc_foreign_org_marker_not_visible(
    org_slug: str, cfg: dict, server_idx: int, foreign_org: str
) -> None:
    """Another org's ORG_ONLY_MARKER env must not appear in this sandbox."""
    from ai_mesh_gateway import mcp_sandbox_client as client

    rpc = await client.broker_send_jsonrpc(
        org_slug,
        cfg,
        "tools/call",
        {"name": "get_env", "arguments": {"key": "ORG_ONLY_MARKER"}},
        msg_id=_msg_id(org_slug, server_idx, 4) + int(foreign_org[-1]),
    )
    text = rpc["result"]["content"][0]["text"]
    if org_slug == foreign_org:
        assert text == f"marker-{foreign_org}"
    else:
        assert text != f"marker-{foreign_org}"


@pytest.mark.docker
@pytest.mark.asyncio
async def test_parallel_5x5_concurrent_tools_list_and_call(broker_url: str):
    """25 endpoints: concurrent tools/list + tools/call with per-org/server identity."""
    t0 = time.perf_counter()
    org_containers = await _prepare_all_orgs()
    assert len(org_containers) == 5

    workloads: list = []
    for org in ORGS:
        for idx, cfg in enumerate(_org_server_configs(org), start=1):
            workloads.append(_rpc_tools_list(org, cfg, idx))
            workloads.append(_rpc_tools_call_echo(org, cfg, idx))
            workloads.append(_rpc_tools_call_org_slug(org, cfg, idx))

    assert len(workloads) == 5 * 5 * 3
    results = await asyncio.gather(*workloads, return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, f"parallel RPC failures: {failures[:3]}"

    elapsed = time.perf_counter() - t0
    print(f"\n[E1] 75 concurrent RPCs across 25 endpoints in {elapsed:.1f}s")


@pytest.mark.docker
@pytest.mark.asyncio
async def test_cross_org_volume_secrets_not_readable(broker_url: str):
    """No org sandbox can read another org's /data/mcp-auth volume secrets."""
    org_containers = await _prepare_all_orgs()

    for org, container_id in org_containers.items():
        secret = f"SECRET_ONLY_{org}"
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

    async def _assert_cannot_read(reader_org: str, owner_org: str) -> None:
        reader_id = org_containers[reader_org]
        if reader_org == owner_org:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    "docker",
                    "exec",
                    reader_id,
                    "cat",
                    f"/data/mcp-auth/secret-{owner_org}.txt",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert proc.returncode == 0
            assert proc.stdout.strip() == f"SECRET_ONLY_{owner_org}"
            return

        proc = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "exec",
                reader_id,
                "sh",
                "-c",
                f"test ! -f /data/mcp-auth/secret-{owner_org}.txt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"{reader_org} must not see {owner_org} volume secret file"
        )

    volume_checks = [
        _assert_cannot_read(reader, owner)
        for reader in ORGS
        for owner in ORGS
    ]
    await asyncio.gather(*volume_checks)

    env_probes = [
        _rpc_foreign_org_marker_not_visible(org, _org_server_configs(org)[0], 1, foreign)
        for org in ORGS
        for foreign in ORGS
    ]
    await asyncio.gather(*env_probes)
