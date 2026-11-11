#!/usr/bin/env python3
"""Ratchet a test run against a checked-in list of known failures.

Legacy suites in this repo carry failures that predate the CI gate. Blocking on
them would keep every PR red; ignoring the suite would let new breakage in. The
ratchet blocks on NEW failures only and reports failures that now pass, so the
baseline can only shrink.

Inputs:
  --log FILE         Django test-runner output (lines "FAIL: name (dotted.id)" / "ERROR: ...")
  --junit FILE       or a pytest junit XML file
  --baseline FILE    one test id per line; '#' comments allowed

Exit codes: 0 = no new failures, 1 = new failures, 2 = bad input or an incomplete/crashed run.
Writes a Markdown summary to $GITHUB_STEP_SUMMARY when it is set.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_DJANGO = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]+)\)")


def from_django_log(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        match = _DJANGO.match(line)
        if match:
            # "(core.tests.mod.Class.test_name)" is the stable, fully qualified id.
            found.add(match.group(3))
    return found


def from_junit(path: Path) -> set[str]:
    # defusedxml refuses entity expansion and external entities; never parse XML with the
    # stdlib parser here, even though the file comes from the same job's test run.
    from defusedxml.ElementTree import parse

    found: set[str] = set()
    for case in parse(path).getroot().iter("testcase"):
        if any(child.tag.split("}")[-1] in {"failure", "error"} for child in case):
            found.add(f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}")
    return found


_RAN = re.compile(r"^Ran (\d+) tests? in ")
_STATUS = re.compile(r"^(OK|FAILED)\b")


def django_completed(text: str) -> int:
    """Tests the Django runner reports it ran, or -1 when the run never reached its summary.

    A run that crashed (no database, an import error, a killed worker) prints no `Ran N tests`
    and no final `OK`/`FAILED` line, and parses as "no failures". It must not pass the ratchet.
    """
    ran = -1
    finished = False
    for line in text.splitlines():
        match = _RAN.match(line)
        if match:
            ran = int(match.group(1))
        elif _STATUS.match(line):
            finished = True
    return ran if finished else -1


def junit_cases(path: Path) -> int:
    from defusedxml.ElementTree import parse

    return sum(1 for _ in parse(path).getroot().iter("testcase"))


def read_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def summarize(new: set[str], fixed: set[str], still: set[str]) -> str:
    lines = ["### Known-failure ratchet", ""]
    lines.append(f"- New failures (blocking): **{len(new)}**")
    lines.append(f"- Known failures still failing: {len(still)}")
    lines.append(f"- Known failures now passing (remove from baseline): {len(fixed)}")
    for title, items in (("New failures", new), ("Now passing", fixed)):
        if items:
            lines += ["", f"<details><summary>{title}</summary>", ""]
            lines += [f"- `{item}`" for item in sorted(items)]
            lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", type=Path)
    source.add_argument("--junit", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument(
        "--exit-code",
        type=int,
        default=None,
        help="the test command's exit status; non-zero with no parsed failure means it crashed",
    )
    parser.add_argument("--min-tests", type=int, default=1, help="fewer tests run means a crash")
    args = parser.parse_args(argv)

    try:
        if args.log is not None:
            text = args.log.read_text(encoding="utf-8", errors="replace")
            failing = from_django_log(text)
            ran = django_completed(text)
        else:
            failing = from_junit(args.junit)
            ran = junit_cases(args.junit)
    except ImportError:
        print("known_failures: --junit needs defusedxml (pip install defusedxml)", file=sys.stderr)
        return 2
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"known_failures: cannot read test output: {exc}", file=sys.stderr)
        return 2

    if ran < args.min_tests:
        print(
            f"known_failures: the test run did not complete (tests reported: {ran}); "
            "a crashed or empty run never passes the ratchet",
            file=sys.stderr,
        )
        print("::error title=Test run incomplete::no test summary, or fewer tests than expected")
        return 2
    if args.exit_code not in (None, 0) and not failing:
        print(
            f"known_failures: the test command exited {args.exit_code} but no failing test "
            "was parsed: treating it as a crash",
            file=sys.stderr,
        )
        print("::error title=Test run crashed::non-zero exit with no parsed test failure")
        return 2

    baseline = read_baseline(args.baseline)
    new = failing - baseline
    fixed = baseline - failing
    still = failing & baseline

    report = summarize(new, fixed, still)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    for item in sorted(new):
        print(f"::error title=New test failure::{item}")
    for item in sorted(fixed):
        print(f"::notice title=Known failure now passes::{item} (remove it from {args.baseline})")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
