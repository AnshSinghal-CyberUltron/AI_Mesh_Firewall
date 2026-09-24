"""Structural gates on rvproto itself (and that they fail on a planted violation)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "rvproto"
LINT = str(ROOT / ".venv" / "bin" / "lint-imports")


def _lint(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([LINT, "--no-cache"], cwd=cwd, capture_output=True, text=True)


def test_layers_kept() -> None:
    r = _lint(ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 kept, 0 broken" in r.stdout


def test_upward_import_is_caught(tmp_path: Path) -> None:
    shutil.copytree(PKG, tmp_path / "rvproto", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    target = tmp_path / "rvproto" / "detect" / "deterministic.py"
    target.write_text("import rvproto.edge.errors  # planted upward import\n" + target.read_text())
    r = subprocess.run([LINT, "--no-cache"], cwd=tmp_path, capture_output=True, text=True,
                       env={"PYTHONPATH": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode != 0 and "BROKEN" in r.stdout


def test_only_resolve_touches_the_dispatch_capability() -> None:
    users = [p for p in PKG.rglob("*.py") if "_RESOLVER_CAPABILITY" in p.read_text()]
    rel = sorted(str(p.relative_to(PKG)) for p in users)
    assert rel == ["domain/decision.py", "resolve/resolver.py"], rel


def test_no_http_response_outside_edge() -> None:
    pat = re.compile(r"http\.response\.start|send_error\(|status_code")
    for p in PKG.rglob("*.py"):
        top = p.relative_to(PKG).parts[0]
        if top in ("edge",):
            continue
        assert not pat.search(p.read_text()), p


def test_repo_gw00_gates_pass_on_rvproto() -> None:
    gw2 = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2")
    py = gw2 / ".venv" / "bin" / "python"
    for gate in ("check_sizes", "check_capacity_literals", "check_http_outside_edge_resolve",
                 "check_no_module_mutable"):
        r = subprocess.run([str(py), "-m", f"lint.{gate}", str(PKG)], cwd=gw2, capture_output=True,
                           text=True)
        assert r.returncode == 0, (gate, r.stdout, r.stderr)
    assert sys.version_info >= (3, 12)
