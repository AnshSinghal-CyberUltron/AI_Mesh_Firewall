#!/usr/bin/env python3
"""GW27 (D-10): is every gateway replica at plan schema 1 before ``controls`` is published?

Scrapes ``plan_schema_version`` from each replica's ``/metrics`` and exits non-zero when any
replica is unreachable, does not export the gauge (an older gateway), or reports a version below
1. The deploy runbook runs this before setting ``V2_PLAN_CONTROLS_ENABLED=true``.

Usage: check_fleet_plan_schema.py http://replica-a:8080/metrics http://replica-b:8080/metrics
"""
from __future__ import annotations

import sys
import urllib.request
from collections.abc import Callable, Sequence

REQUIRED_VERSION = 1
GAUGE_SUFFIX = "plan_schema_version"


def parse_version(exposition: str) -> int | None:
    """The gauge value in Prometheus text, or None when the replica does not export it."""
    for line in exposition.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        name = parts[0].split("{", 1)[0]
        if len(parts) >= 2 and name.endswith(GAUGE_SUFFIX):
            try:
                return int(float(parts[1]))
            except ValueError:
                return None
    return None


def evaluate(results: dict[str, str | None]) -> list[str]:
    """Problems, one per replica that is missing, unreachable or below the required version."""
    problems: list[str] = []
    if not results:
        return ["no replicas given: refusing to certify an empty fleet"]
    for target, text in sorted(results.items()):
        if text is None:
            problems.append(f"{target}: unreachable")
            continue
        version = parse_version(text)
        if version is None:
            problems.append(f"{target}: does not export {GAUGE_SUFFIX} (older gateway)")
        elif version < REQUIRED_VERSION:
            problems.append(f"{target}: {GAUGE_SUFFIX}={version} < {REQUIRED_VERSION}")
    return problems


def fetch(url: str, timeout_s: float = 5.0) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return str(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - any failure to fetch is an unreachable replica
        return None


def main(argv: Sequence[str] | None = None, fetcher: Callable[[str], str | None] = fetch) -> int:
    targets = list(sys.argv[1:] if argv is None else argv)
    problems = evaluate({target: fetcher(target) for target in targets})
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"all {len(targets)} replicas export {GAUGE_SUFFIX} >= {REQUIRED_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
