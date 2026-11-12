"""Deterministic file-level pytest sharding, and the check that the shards ran the whole suite.

Pytest plugin (``scripts/ci`` on ``PYTHONPATH``)::

    pytest -p amf_pytest_shards --shard-index 0 --shard-count 8 \\
        --shard-weights ../.github/ci/gateway-v2-test-durations.json \\
        --shard-report "$RUNNER_TEMP/shard-0.json"

Every shard collects the WHOLE suite, assigns whole test files to shards, and deselects the
tests of every other shard. The assignment depends only on the collected test ids and the
weights file, both identical in every shard of one run, so the shards partition the suite:
each collected test runs in exactly one shard. Whole files stay together, so module-scoped
fixtures and in-file test order behave as in an unsharded run.

Assignment: longest-processing-time first. Files are sorted by weight (descending, then by
path) and each goes to the currently lightest shard (lowest index on a tie). A file's weight
is its measured duration from the weights file; a file missing from it weighs
``tests in the file * per_test_default``. Weights only balance the shards: a stale or missing
weights file never drops or duplicates a test.

``--shard-report`` writes JSON: the full collection's size and SHA-256, the selected test ids,
outcome counts, per-file durations and the slowest tests. The aggregate CI job checks the
reports with the stdlib-only CLI below; it needs no pytest::

    python3 scripts/ci/amf_pytest_shards.py verify REPORT_DIR    # fails unless the shards
                                                                 # partition one collection
    python3 scripts/ci/amf_pytest_shards.py weights REPORT_DIR   # weights file on stdout

``verify`` fails when: no report; shards disagree on the shard count, the collection or the
assignment; a shard index is missing or repeated; two shards selected the same test; the
union of the selections is not the collection; or a shard's pytest exit status is not 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:  # the plugin hooks need pytest; the CLI and its self-tests do not
    import pytest

    _trylast = pytest.hookimpl(trylast=True)
except ImportError:  # pragma: no cover - exercised by the stdlib-only self-tests

    def _trylast(func: Any) -> Any:
        return func


REPORT_FORMAT = 1
WEIGHTS_FORMAT = 1
SLOWEST_KEPT = 30
# pytest's exit status when no test was selected; an empty shard is not a failure.
_NO_TESTS_COLLECTED = 5


# --------------------------------------------------------------------------- pure functions


def file_of(nodeid: str) -> str:
    """The file of a node id: ``tests/a/test_x.py::C::t[p]`` -> ``tests/a/test_x.py``."""
    return nodeid.split("::", 1)[0]


def digest(nodeids: Iterable[str]) -> str:
    """Order-independent SHA-256 of a set of node ids."""
    return hashlib.sha256("\n".join(sorted(nodeids)).encode("utf-8")).hexdigest()


def file_weights(
    tests_per_file: Mapping[str, int], weights: Mapping[str, Any] | None
) -> dict[str, float]:
    """Weight of every collected file: measured seconds, else ``tests * per_test_default``."""
    measured: Mapping[str, Any] = (weights or {}).get("files", {})
    per_test = float((weights or {}).get("per_test_default", 1.0))
    result: dict[str, float] = {}
    for path, n_tests in tests_per_file.items():
        value = measured.get(path)
        result[path] = (
            float(value) if isinstance(value, (int, float)) and value >= 0 else n_tests * per_test
        )
    return result


def assign(weights: Mapping[str, float], count: int) -> dict[str, int]:
    """Map each file to a shard in ``range(count)``: longest-processing-time first."""
    if count < 1:
        raise ValueError(f"shard count must be >= 1, got {count}")
    loads = [0.0] * count
    shard_of: dict[str, int] = {}
    for path in sorted(weights, key=lambda p: (-weights[p], p)):
        target = min(range(count), key=lambda i: (loads[i], i))
        shard_of[path] = target
        loads[target] += weights[path]
    return shard_of


def assignment_digest(shard_of: Mapping[str, int]) -> str:
    return hashlib.sha256(
        "\n".join(f"{path}\t{shard_of[path]}" for path in sorted(shard_of)).encode("utf-8")
    ).hexdigest()


def select(
    nodeids: Sequence[str], index: int, count: int, weights: Mapping[str, Any] | None
) -> tuple[list[str], dict[str, int]]:
    """The node ids of shard ``index`` (in collection order) and the full file assignment."""
    if not 0 <= index < count:
        raise ValueError(f"shard index {index} is outside 0..{count - 1}")
    per_file: dict[str, int] = {}
    for nodeid in nodeids:
        per_file[file_of(nodeid)] = per_file.get(file_of(nodeid), 0) + 1
    shard_of = assign(file_weights(per_file, weights), count)
    return [n for n in nodeids if shard_of[file_of(n)] == index], shard_of


def load_weights(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("format") != WEIGHTS_FORMAT or not isinstance(doc.get("files"), dict):
        raise ValueError(f"{path}: not a format-{WEIGHTS_FORMAT} weights file")
    return dict(doc)


# --------------------------------------------------------------------------- pytest plugin


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("amf-shards", "deterministic file-level sharding (scripts/ci)")
    group.addoption("--shard-index", type=int, default=None, help="0-based index of this shard")
    group.addoption("--shard-count", type=int, default=None, help="number of shards")
    group.addoption("--shard-weights", default=None, help="weights JSON (per-file seconds)")
    group.addoption("--shard-report", default=None, help="write this shard's JSON report here")


class _Shard:
    def __init__(
        self, index: int, count: int, weights_path: str | None, report_path: str | None
    ) -> None:
        self.index, self.count = index, count
        self.weights_path, self.report_path = weights_path, report_path
        self.weights = load_weights(weights_path)
        self.started = time.monotonic()
        self.collected: list[str] = []
        self.selected: list[str] = []
        self.assignment = ""
        self.files: dict[str, float] = {}
        self.per_test: dict[str, float] = {}
        self.outcomes: dict[str, int] = {}

    @_trylast
    def pytest_collection_modifyitems(self, config: Any, items: list[Any]) -> None:
        self.collected = [item.nodeid for item in items]
        selected, shard_of = select(self.collected, self.index, self.count, self.weights)
        self.selected, self.assignment = selected, assignment_digest(shard_of)
        keep = set(selected)
        deselected = [item for item in items if item.nodeid not in keep]
        items[:] = [item for item in items if item.nodeid in keep]
        if deselected:
            config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report: Any) -> None:
        path = file_of(report.nodeid)
        self.files[path] = self.files.get(path, 0.0) + float(report.duration)
        self.per_test[report.nodeid] = self.per_test.get(report.nodeid, 0.0) + float(
            report.duration
        )
        if report.when == "call":
            if hasattr(report, "wasxfail"):
                outcome = "xfailed" if report.skipped else "xpassed"
            else:
                outcome = str(report.outcome)
        elif report.failed:
            outcome = "error"
        elif report.skipped and report.when == "setup":
            outcome = "skipped"
        else:
            return
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        status = int(exitstatus)
        if status == _NO_TESTS_COLLECTED and self.collected and not self.selected:
            session.exitstatus = status = 0
        if not self.report_path:
            return
        slowest = sorted(self.per_test.items(), key=lambda kv: (-kv[1], kv[0]))[:SLOWEST_KEPT]
        report = {
            "format": REPORT_FORMAT,
            "index": self.index,
            "count": self.count,
            "collected": len(self.collected),
            "collected_sha256": digest(self.collected),
            "assignment_sha256": self.assignment,
            "weights_file": self.weights_path,
            "selected": sorted(self.selected),
            "outcomes": dict(sorted(self.outcomes.items())),
            "files": {p: round(s, 3) for p, s in sorted(self.files.items())},
            "slowest": [[n, round(s, 3)] for n, s in slowest],
            "exitstatus": status,
            "wall_seconds": round(time.monotonic() - self.started, 1),
        }
        target = Path(self.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")


def pytest_configure(config: Any) -> None:
    count = config.getoption("--shard-count")
    index = config.getoption("--shard-index")
    if count is None and index is None:
        return
    if count is None or index is None:
        raise ValueError("--shard-index and --shard-count go together")
    if count < 1 or not 0 <= index < count:
        raise ValueError(f"--shard-index {index} is outside 0..{count - 1}")
    shard = _Shard(
        index, count, config.getoption("--shard-weights"), config.getoption("--shard-report")
    )
    config.pluginmanager.register(shard, "amf-shard")


# --------------------------------------------------------------------------- aggregate CLI


class VerifyError(Exception):
    pass


def read_reports(directory: Path) -> list[dict[str, Any]]:
    reports = []
    for path in sorted(directory.rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != REPORT_FORMAT:
            raise VerifyError(f"{path}: not a format-{REPORT_FORMAT} shard report")
        doc["_path"] = str(path)
        reports.append(doc)
    return reports


def verify(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check that the reports partition one collection; return the merged totals."""
    if not reports:
        raise VerifyError("no shard reports found")
    first = reports[0]
    for key in ("count", "collected", "collected_sha256", "assignment_sha256"):
        values = {r[key] for r in reports}
        if len(values) != 1:
            raise VerifyError(f"shards disagree on {key}: {sorted(map(str, values))}")
    count = int(first["count"])
    indices = sorted(int(r["index"]) for r in reports)
    if indices != list(range(count)):
        raise VerifyError(f"expected one report per shard index 0..{count - 1}, got {indices}")
    seen: dict[str, int] = {}
    for r in reports:
        for nodeid in r["selected"]:
            if nodeid in seen:
                raise VerifyError(f"{nodeid} selected by shards {seen[nodeid]} and {r['index']}")
            seen[nodeid] = int(r["index"])
    if len(seen) != int(first["collected"]) or digest(seen) != first["collected_sha256"]:
        raise VerifyError(
            f"the shards selected {len(seen)} tests; the collection has {first['collected']} "
            "(or the same number of different ids)"
        )
    bad = [
        f"shard {r['index']}: exit {r['exitstatus']}" for r in reports if int(r["exitstatus"]) != 0
    ]
    if bad:
        raise VerifyError("pytest failed in " + ", ".join(bad))
    outcomes: dict[str, int] = {}
    for r in reports:
        for name, n in r["outcomes"].items():
            outcomes[name] = outcomes.get(name, 0) + int(n)
    slowest = sorted(
        (tuple(pair) for r in reports for pair in r["slowest"]), key=lambda p: (-p[1], p[0])
    )
    return {
        "count": count,
        "collected": int(first["collected"]),
        "outcomes": dict(sorted(outcomes.items())),
        "shards": [
            {
                "index": int(r["index"]),
                "tests": len(r["selected"]),
                "files": len(r["files"]),
                "measured_seconds": round(sum(r["files"].values()), 1),
                "wall_seconds": r["wall_seconds"],
            }
            for r in sorted(reports, key=lambda r: int(r["index"]))
        ],
        "slowest": [list(p) for p in slowest[:SLOWEST_KEPT]],
    }


def weights_doc(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files: dict[str, float] = {}
    for r in reports:
        for path, seconds in r["files"].items():
            files[path] = files.get(path, 0.0) + float(seconds)
    tests = sum(len(r["selected"]) for r in reports)
    total = sum(files.values())
    return {
        "format": WEIGHTS_FORMAT,
        "about": "Per-file pytest seconds (setup+call+teardown) from one CI run; balances "
        "scripts/ci/amf_pytest_shards.py. Regenerate with its `weights` command.",
        "per_test_default": round(total / tests, 4) if tests else 1.0,
        "files": {path: round(files[path], 1) for path in sorted(files)},
    }


def _summary_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        f"### pytest shards: {result['collected']} tests in {result['count']} shards, "
        "each selected exactly once",
        "",
        "Outcomes: " + ", ".join(f"{n} {k}" for k, n in result["outcomes"].items()),
        "",
        "| shard | tests | files | test seconds | pytest wall seconds |",
        "|---:|---:|---:|---:|---:|",
    ]
    for s in result["shards"]:
        cells = (s["index"], s["tests"], s["files"], s["measured_seconds"], s["wall_seconds"])
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    lines += ["", "Slowest tests (setup+call+teardown seconds):", ""]
    lines += [f"- {seconds:.2f} `{nodeid}`" for nodeid, seconds in result["slowest"][:15]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "weights"):
        p = sub.add_parser(name)
        p.add_argument("reports", type=Path, help="directory holding the shard report JSON files")
    args = parser.parse_args(argv)
    try:
        reports = read_reports(args.reports)
        if args.command == "weights":
            verify(reports)
            print(json.dumps(weights_doc(reports), indent=1))
            return 0
        result = verify(reports)
    except (VerifyError, KeyError, ValueError, TypeError) as exc:
        print(f"::error title=pytest shards::{exc}")
        return 1
    text = _summary_markdown(result)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
