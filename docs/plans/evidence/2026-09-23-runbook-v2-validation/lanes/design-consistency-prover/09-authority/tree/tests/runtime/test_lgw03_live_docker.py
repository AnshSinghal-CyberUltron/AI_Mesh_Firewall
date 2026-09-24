"""Live docker --cpus proofs for LGW03-1/2/3/5. Skipped unless AMF_LIVE_DOCKER=1."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AMF_LIVE_DOCKER") != "1",
    reason="set AMF_LIVE_DOCKER=1 to rebuild the v2 image and run --cpus proofs",
)

REPO = Path(__file__).resolve().parents[3]
IMAGE = os.environ.get("AMF_GW03_IMAGE", "ai-mesh-gateway-v2:gw03")


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def image() -> str:
    _run(
        [
            "docker",
            "buildx",
            "build",
            "--provenance=false",
            "--sbom=false",
            "--load",
            "--build-arg",
            "SOURCE_DATE_EPOCH=1704067200",
            "-f",
            str(REPO / "gateway_v2" / "Dockerfile"),
            "-t",
            IMAGE,
            str(REPO),
        ],
        timeout=300,
    )
    return IMAGE


def _json_run(image: str, extra: list[str]) -> dict[str, Any]:
    proc = _run(
        ["docker", "run", "--rm", *extra, image, "python", "-m", "gateway_v2.runtime"],
    )
    text = proc.stdout.strip()
    return json.loads(text)


def test_lgw03_1_live_cpus(image: str) -> None:
    rows = {}
    for n in (2, 4, 8, 16):
        rows[n] = _json_run(image, [f"--cpus={n}"])
    workers = {rows[n]["workers"] for n in rows}
    queues = {rows[n]["queue_depth"] for n in rows}
    scanners = {rows[n]["pools"]["scanner"] for n in rows}
    assert len(workers) == 4, rows
    assert len(queues) == 4, rows
    assert len(scanners) == 4, rows


def test_lgw03_2_live_memory(image: str) -> None:
    env = ["-e", "AMF_PER_WORKER_RSS_MB=400"]
    high = _json_run(image, ["--cpus=8", "--memory=1600m", *env])
    low = _json_run(image, ["--cpus=8", "--memory=800m", *env])
    assert low["stream_buffer_bytes"] < high["stream_buffer_bytes"]
    assert low["queue_depth"] < high["queue_depth"]
    assert low["workers"] <= high["workers"]


def test_lgw03_3_live_fd(image: str) -> None:
    high = _json_run(image, ["--cpus=4", "--ulimit", "nofile=65536:65536"])
    low = _json_run(image, ["--cpus=4", "--ulimit", "nofile=256:256"])
    assert low["connection_budget"] < high["connection_budget"]
    assert low["fd_limit"] < high["fd_limit"]


def test_lgw03_5_live_sighup(image: str) -> None:
    name = f"gw03-sighup-{os.getpid()}"
    try:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--cpus=2",
                "-e",
                "AMF_HOLD_LEASE=1",
                image,
                "python",
                "-m",
                "gateway_v2.runtime",
                "--serve",
            ],
        )
        time.sleep(1.5)
        inspect = json.loads(_run(["docker", "inspect", name]).stdout)[0]
        pid_before = inspect["State"]["Pid"]
        assert inspect["State"]["Running"] is True
        first = _first_json(_run(["docker", "logs", name]).stdout)
        assert first["cpu_quota"] == pytest.approx(2.0, abs=0.15)
        assert first["workers"] == 1
        _run(["docker", "update", "--cpus=8", name])
        _run(["docker", "kill", "--signal=HUP", name])
        deadline = time.monotonic() + 15
        latest = first
        while time.monotonic() < deadline:
            latest = _last_json(_run(["docker", "logs", name]).stdout)
            if latest.get("reload_count", 0) >= 1:
                break
            time.sleep(0.4)
        inspect2 = json.loads(_run(["docker", "inspect", name]).stdout)[0]
        assert inspect2["State"]["Pid"] == pid_before
        assert latest["reload_count"] >= 1
        assert latest["dropped"] == 0
        assert latest["in_flight"] >= 1
        assert latest["workers"] != first["workers"]
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def _first_json(logs: str) -> dict[str, Any]:
    chunks = _json_chunks(logs)
    assert chunks, logs
    return chunks[0]


def _last_json(logs: str) -> dict[str, Any]:
    chunks = _json_chunks(logs)
    assert chunks, logs
    return chunks[-1]


def _json_chunks(logs: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    buf: list[str] = []
    for line in logs.splitlines():
        if line.strip() == "{":
            buf = [line]
            continue
        if buf:
            buf.append(line)
            if line.strip() == "}":
                found.append(json.loads("\n".join(buf)))
                buf = []
    return found
