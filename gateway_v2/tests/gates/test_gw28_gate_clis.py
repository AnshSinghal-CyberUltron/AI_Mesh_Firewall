"""GW28 D-15 regression guard: the size and capacity-literal gate CLIs stay green."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("gate", ["check_sizes", "check_capacity_literals"])
def test_gate_cli_exits_zero_on_the_data_plane(gate: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", f"lint.{gate}", "gateway_v2"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
