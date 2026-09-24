"""LGW00-3: detect/ must not import edge/; import-linter names the contract."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LAYERS = (
    "edge",
    "admit",
    "plan",
    "detect",
    "resolve",
    "dispatch",
    "egress",
    "audit",
    "runtime",
    "contracts",
)

_PYPROJECT = """
[tool.importlinter]
root_packages = ["gateway_v2"]

[[tool.importlinter.contracts]]
name = "Gateway v2 layers"
type = "layers"
layers = [
    "gateway_v2.edge",
    "gateway_v2.admit",
    "gateway_v2.plan",
    "gateway_v2.detect",
    "gateway_v2.resolve",
    "gateway_v2.dispatch",
    "gateway_v2.egress",
    "gateway_v2.audit",
    "gateway_v2.runtime",
    "gateway_v2.contracts",
]
"""


def _write_layers(pkg: Path) -> None:
    (pkg / "__init__.py").write_text('"""mini v2."""\n', encoding="utf-8")
    for layer in LAYERS:
        d = pkg / layer
        d.mkdir()
        (d / "__init__.py").write_text(f'"""{layer}."""\n', encoding="utf-8")
    (pkg / "edge" / "app.py").write_text("app = None\n", encoding="utf-8")
    (pkg / "detect" / "base.py").write_text('"""findings only."""\n', encoding="utf-8")


def _lint(cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(cwd)}
    lint_imports = Path(sys.prefix) / "bin" / "lint-imports"
    cmd = [str(lint_imports)] if lint_imports.is_file() else [sys.executable, "-m", "importlinter"]
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_empty_layers_pass_import_linter(tmp_path: Path) -> None:
    pkg = tmp_path / "gateway_v2"
    pkg.mkdir()
    _write_layers(pkg)
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    result = _lint(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_lgw00_3_upward_import_fails_naming_contract(tmp_path: Path) -> None:
    pkg = tmp_path / "gateway_v2"
    pkg.mkdir()
    _write_layers(pkg)
    (pkg / "detect" / "base.py").write_text(
        "from gateway_v2.edge import app\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    result = _lint(tmp_path)
    dumped = (result.stdout + result.stderr).lower()
    assert result.returncode != 0
    assert "gateway v2 layers" in dumped or "layers" in dumped
    assert "detect" in dumped or "edge" in dumped
