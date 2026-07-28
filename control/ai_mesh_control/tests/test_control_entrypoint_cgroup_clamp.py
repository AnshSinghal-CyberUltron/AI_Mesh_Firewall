"""Entrypoint cgroup clamp: workers must never exceed ceil(cpu_budget)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "server-entrypoint.sh"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(not ENTRYPOINT.is_file(), reason="server-entrypoint.sh missing")
def test_entrypoint_clamps_workers_to_cgroup_cpu():
    """CONTROL_WEB_CONCURRENCY=8 on a 2-CPU budget must clamp to 2."""
    env = os.environ.copy()
    env["CONTROL_WEB_CONCURRENCY"] = "8"
    env["CONTROL_ENTRYPOINT_DRYRUN"] = "1"
    env["RESOURCE_BUDGET_CPUS"] = "2"
    env["RESOURCE_BUDGET_MEM_GB"] = "2"
    shared = str(REPO_ROOT / "shared")
    env["PYTHONPATH"] = shared + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    result = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        cwd=str(REPO_ROOT / "control"),
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "clamping workers 8 → 2" in combined
    assert "--workers 2" in combined or "workers=2" in combined
