#!/usr/bin/env python3
"""Fail when a pytest JUnit file shows a test that hit the per-test timeout (pytest-timeout).

openai-conformance's LGW01 step runs its suite twice and tolerates pytest exit 1: failures that
both runs record are the conformance ledger, and only drift between the runs fails the step
(``gateway_v2/lint/compare_junit.py``). A hang is not a ledger entry. Without pytest-timeout,
a hang ran into the job's ``timeout-minutes`` and the check went red. With it, the hang is a
failed test in both runs, the runs agree, and the check would go green (Reviewer B, PR #137
S2). The step runs this script on both JUnit files, so a hang stays red and is named.

A test hit the timeout when either holds:

1. A ``failure`` or ``error`` element carries pytest-timeout's message, ``Timeout (>300.0s)
   from pytest-timeout.``, in its ``message`` attribute or its text. This covers a hang in
   setup, call or teardown, and a test whose own ``@pytest.mark.timeout(n)`` is below the limit.
2. The test's JUnit ``time`` is at least the configured limit, less a slack. pytest-timeout
   times setup, call and teardown in one window (``timeout_func_only`` false), and JUnit's
   ``time`` is the sum of those phases (``junit_duration_report = "total"``), so a test this
   long had its timer fire, whatever outcome it reports. This covers what the message cannot
   show: a non-strict ``xfail`` turns the timeout into XFAIL, and JUnit then records only the
   xfail reason; a test that catches ``BaseException`` passes. The slack (1 s, or a tenth of a
   shorter limit) covers the hook time between phases, which the phase durations leave out. A
   test that legitimately runs that close to the limit is reported too: red, the safe side.

The limit is pytest-timeout's own: ``PYTEST_TIMEOUT`` when set, else the ``timeout`` option of
``--config`` (``[tool.pytest.ini_options]`` of a ``pyproject.toml``). A command-line
``--timeout`` is not seen, so the caller must not pass one. When the config sets
``timeout_func_only`` or another ``junit_duration_report``, rule 2 no longer holds and the
script refuses to judge.

pytest-timeout's ``thread`` method ends pytest with ``os._exit(1)`` and no JUnit file is
written: the caller removes stale files before the runs, so that run fails on the missing file.

Exit codes: 0 = no test hit the timeout; 1 = at least one did, each named; 2 = cannot judge (a
missing, unreadable or malformed file, a DOCTYPE, a bad ``time``, or a config that breaks
rule 2).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path

# pytest-timeout >= 2.0: PYTEST_FAILURE_MESSAGE = "Timeout (>%ss) from pytest-timeout."
TIMEOUT_MESSAGE = re.compile(r"Timeout \(>[^)]*\) from pytest-timeout")
MAX_SLACK_SECONDS = 1.0
# pytest's own bool ini parsing (_strtobool), which pytest-timeout's ini options go through.
_TRUE = frozenset({"y", "yes", "t", "true", "on", "1"})


class CannotJudge(Exception):
    """An input this check cannot judge. The caller must fail, never pass."""


def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def per_test_limit(config: Path, environ: Mapping[str, str]) -> float | None:
    """pytest-timeout's per-test limit in seconds, or None when no timeout is configured."""
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise CannotJudge(f"{config}: {exc}") from exc
    tool = data.get("tool")
    pytest_table = tool.get("pytest") if isinstance(tool, dict) else None
    ini = pytest_table.get("ini_options") if isinstance(pytest_table, dict) else None
    if not isinstance(ini, dict):
        raise CannotJudge(f"{config}: no [tool.pytest.ini_options] table; is it the run's config?")
    if str(ini.get("timeout_func_only", "false")).strip().lower() in _TRUE:
        raise CannotJudge(
            f"{config}: timeout_func_only is set, so the timer no longer covers setup and "
            "teardown while JUnit's time does; the duration rule cannot tell a hang"
        )
    if str(ini.get("junit_duration_report", "total")) != "total":
        raise CannotJudge(
            f"{config}: junit_duration_report is not 'total', so JUnit's time is not the timed "
            "window; the duration rule cannot tell a hang"
        )
    # Same precedence as pytest-timeout's get_env_settings: PYTEST_TIMEOUT, then the ini value.
    raw: object = environ.get("PYTEST_TIMEOUT")
    where = "PYTEST_TIMEOUT"
    if raw is None:
        raw, where = ini.get("timeout"), f"{config} timeout"
        if raw is None or raw == "":
            return None
    try:
        limit = float(str(raw))
    except ValueError as exc:
        raise CannotJudge(f"{where} is not a number: {raw!r}") from exc
    if not math.isfinite(limit):
        raise CannotJudge(f"{where} is not finite: {raw!r}")
    return limit if limit > 0 else None


def _outcome(case: ET.Element) -> str:
    for child in case:
        tag = _tag(child)
        if tag == "skipped" and child.get("type") == "pytest.xfail":
            return "xfail"
        if tag in {"failure", "error", "skipped"}:
            return tag
    return "passed"


def timeouts_in(path: Path, limit: float | None) -> tuple[int, list[str]]:
    """Return (test cases in the file, one line per test that hit the timeout)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CannotJudge(f"{path}: {exc.strerror or exc}") from exc
    # pytest never writes a DOCTYPE. Refusing one leaves the stdlib parser nothing to expand
    # (internal and external entities both need one); this venv has no defusedxml.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise CannotJudge(f"{path}: has a DOCTYPE or an ENTITY declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise CannotJudge(f"{path}: not well-formed XML: {exc}") from exc
    threshold = None if limit is None else limit - min(MAX_SLACK_SECONDS, limit / 10)
    cases = 0
    hits: list[str] = []
    for case in root.iter():
        if _tag(case) != "testcase":
            continue
        cases += 1
        test = f"{case.get('classname', '')}::{case.get('name', '')}"
        raw_time = case.get("time")
        try:
            seconds = float(raw_time) if raw_time is not None else math.nan
        except ValueError:
            seconds = math.nan
        if not math.isfinite(seconds) or seconds < 0:
            raise CannotJudge(f"{path}: {test}: time is {raw_time!r}, not seconds >= 0")
        message = None
        for child in case:
            if _tag(child) in {"failure", "error"}:
                match = TIMEOUT_MESSAGE.search(f"{child.get('message', '')}\n{child.text or ''}")
                if match:
                    message = f"{_tag(child)} '{match.group(0)}'"
                    break
        if message is not None:
            hits.append(f"{path}: {test}: {message}")
        elif threshold is not None and seconds >= threshold:
            hits.append(
                f"{path}: {test}: ran {seconds:.3f} s against a per-test timeout of {limit:g} s "
                f"(reported {_outcome(case)}), so its timer fired"
            )
    return cases, hits


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a pytest JUnit file shows a test that hit the per-test timeout."
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="the pyproject.toml the pytest runs used"
    )
    parser.add_argument("junit", type=Path, nargs="+", help="pytest --junitxml files")
    args = parser.parse_args(argv)
    try:
        limit = per_test_limit(args.config, os.environ)
        cases = 0
        hits: list[str] = []
        for path in args.junit:
            found_cases, found = timeouts_in(path, limit)
            cases += found_cases
            hits += found
    except CannotJudge as exc:
        print(f"junit_timeouts: cannot judge, failing: {exc}", file=sys.stderr)
        return 2
    for hit in hits:
        print(f"junit_timeouts FAIL {hit}")
    if hits:
        print(
            f"junit_timeouts FAIL: {len(hits)} test result(s) hit the per-test timeout. A hang "
            "is not a recorded failure, even when every run agrees."
        )
        return 1
    shown = "off" if limit is None else f"{limit:g}s"
    print(f"junit_timeouts_ok files={len(args.junit)} cases={cases} limit={shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
