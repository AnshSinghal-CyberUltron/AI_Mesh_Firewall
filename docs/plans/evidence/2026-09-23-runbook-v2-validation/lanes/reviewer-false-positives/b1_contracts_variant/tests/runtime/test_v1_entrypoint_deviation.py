"""LGW03-6: v1 gateway/entrypoint.sh logs WEB_CONCURRENCY as a named deviation."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "gateway" / "entrypoint.sh"


def test_v1_entrypoint_logs_override_and_detected(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "python"
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = {
        **os.environ,
        "WEB_CONCURRENCY": "9",
        "GATEWAY_ENTRYPOINT_DRYRUN": "1",
        "PATH": f"{bindir}:{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(REPO / "shared"),
    }
    proc = subprocess.run(
        ["sh", str(ENTRY)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )
    err = proc.stderr
    assert proc.returncode == 0, err
    assert "WEB_CONCURRENCY override=9" in err
    assert "detected=" in err
    assert "(deviation)" in err
