"""L34-1 matrix reporting: per client x cell ran/passed/skipped(reason), written to the evidence.

A skipped cell is never read as a pass: the report lists ``ran`` and ``skipped`` separately and
the exit note refuses to claim L34-1 while the Python or Node loopback cells have ``ran == 0``.
Test tooling only; the report merges into any existing file so a partial run (e.g. only the Node
cells) does not erase the others.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVIDENCE = _REPO_ROOT / "docs" / "plans" / "evidence" / os.environ.get(
    "AMF_GW34_EVIDENCE_DIR", "2026-11-04-gw34"
)
_CLIENTS = ("python-sync", "python-async", "node")
_CELLS = tuple("abcdefghij")
_RESULTS: dict[tuple[str, str], dict[str, Any]] = {}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "matrix(client, cell): an L34-1 matrix cell")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    outcome = yield
    report = outcome.get_result()
    mark = item.get_closest_marker("matrix")
    if mark is None or report.when not in ("setup", "call"):
        return
    callspec = getattr(item, "callspec", None)
    client = (callspec.params.get("client") if callspec else None) or mark.kwargs["client"]
    cell = (callspec.params.get("cell") if callspec else None) or mark.kwargs["cell"]
    key = (str(client), str(cell))
    if hasattr(report, "wasxfail"):  # a KNOWN failure, listed apart from passes and skips
        entry = _RESULTS.setdefault(key, {"ran": 0, "passed": 0, "skipped": None})
        if report.when == "call":
            entry["ran"] += 1
            entry["xfail"] = str(report.wasxfail) or "known failure"
        return
    if report.skipped:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        _RESULTS.setdefault(key, {"ran": 0, "passed": 0, "skipped": reason})
    elif report.when == "call":
        entry = _RESULTS.setdefault(key, {"ran": 0, "passed": 0, "skipped": None})
        entry["ran"] += 1
        entry["passed"] += int(report.passed)
        entry["skipped"] = None


def _versions() -> dict[str, str]:
    versions = {"python": platform.python_version(), "environment": "host"}
    try:
        import openai

        versions["openai_python"] = openai.__version__
    except ImportError:
        versions["openai_python"] = "absent"
    node = shutil.which("node")
    if node:
        versions["node"] = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
    package = (
        Path(__file__).resolve().parents[1] / "node" / "node_modules" / "openai" / "package.json"
    )
    if package.exists():
        versions["openai_node"] = json.loads(package.read_text())["version"]
    return versions


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _RESULTS:
        return
    path = _EVIDENCE / "sdk-matrix-report.json"
    report: dict[str, Any] = {"clients": {}}
    if path.exists():
        report = json.loads(path.read_text())
    for client in _CLIENTS:
        cells = report["clients"].setdefault(client, {})
        for cell in _CELLS:
            if (client, cell) in _RESULTS:
                cells[cell] = _RESULTS[(client, cell)]
            else:
                cells.setdefault(
                    cell, {"ran": 0, "passed": 0, "skipped": "not selected in this run"}
                )
    report["environment"] = _versions()
    _EVIDENCE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if not _RESULTS:
        return
    terminalreporter.write_sep("-", "L34-1 matrix (ran/passed per client x cell)")
    for client in _CLIENTS:
        row = []
        for cell in _CELLS:
            entry = _RESULTS.get((client, cell))
            row.append(f"{cell}:{entry['passed']}/{entry['ran']}" if entry else f"{cell}:-")
        terminalreporter.write_line(f"{client:13s} " + " ".join(row))
