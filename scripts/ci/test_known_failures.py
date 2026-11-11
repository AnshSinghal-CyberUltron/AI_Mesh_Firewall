"""Self-tests for known_failures.py (run by the CI workflow's ci-scripts job)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import known_failures  # noqa: E402

_LOG = """\
FAIL: test_a (core.tests.mod.ATests.test_a)
----------------------------------------------------------------------
ERROR: test_b (policy.tests.mod.BTests.test_b)
FAIL: test_c (core.tests.mod.ATests.test_c)
----------------------------------------------------------------------
Ran 3 tests in 0.004s

FAILED (failures=2, errors=1)
"""

_CRASH = """\
Creating test database for alias 'default'...
django.db.utils.OperationalError: connection to server at "127.0.0.1", port 5432 failed
"""

_JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite>
  <testcase classname="tests.x" name="test_ok"/>
  <testcase classname="tests.x" name="test_fail"><failure message="boom"/></testcase>
  <testcase classname="tests.y" name="test_err"><error message="boom"/></testcase>
  <testcase classname="tests.y" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>
"""


class KnownFailuresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def _write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_django_log_ids(self) -> None:
        self.assertEqual(
            known_failures.from_django_log(_LOG),
            {
                "core.tests.mod.ATests.test_a",
                "policy.tests.mod.BTests.test_b",
                "core.tests.mod.ATests.test_c",
            },
        )

    def test_junit_counts_failures_and_errors_not_skips(self) -> None:
        self.assertEqual(
            known_failures.from_junit(self._write("j.xml", _JUNIT)),
            {"tests.x::test_fail", "tests.y::test_err"},
        )

    def test_all_known_passes(self) -> None:
        baseline = self._write(
            "b.txt",
            "# comment\ncore.tests.mod.ATests.test_a\npolicy.tests.mod.BTests.test_b\n"
            "core.tests.mod.ATests.test_c\n",
        )
        log = self._write("log.txt", _LOG)
        self.assertEqual(known_failures.main(["--log", str(log), "--baseline", str(baseline)]), 0)

    def test_new_failure_blocks(self) -> None:
        baseline = self._write("b.txt", "core.tests.mod.ATests.test_a\n")
        log = self._write("log.txt", _LOG)
        self.assertEqual(known_failures.main(["--log", str(log), "--baseline", str(baseline)]), 1)

    def test_fixed_known_failure_does_not_block(self) -> None:
        baseline = self._write(
            "b.txt",
            "core.tests.mod.ATests.test_a\npolicy.tests.mod.BTests.test_b\n"
            "core.tests.mod.ATests.test_c\ncore.tests.gone.Tests.test_fixed\n",
        )
        log = self._write("log.txt", _LOG)
        self.assertEqual(known_failures.main(["--log", str(log), "--baseline", str(baseline)]), 0)

    def test_a_crashed_run_never_passes(self) -> None:
        """Review of #102: no database, 0 tests run, exit 1 must not read as "no new failures"."""
        baseline = self._write("b.txt", "")
        log = self._write("log.txt", _CRASH)
        self.assertEqual(known_failures.main(["--log", str(log), "--baseline", str(baseline)]), 2)
        empty = self._write("empty.txt", "")
        self.assertEqual(known_failures.main(["--log", str(empty), "--baseline", str(baseline)]), 2)

    def test_a_non_zero_exit_without_a_parsed_failure_is_a_crash(self) -> None:
        baseline = self._write("b.txt", "")
        ok_log = self._write("ok.txt", "Ran 4 tests in 0.1s\n\nOK\n")
        args = ["--log", str(ok_log), "--baseline", str(baseline)]
        self.assertEqual(known_failures.main([*args, "--exit-code", "0"]), 0)
        self.assertEqual(known_failures.main([*args, "--exit-code", "1"]), 2)

    def test_an_empty_junit_report_is_a_crash(self) -> None:
        baseline = self._write("b.txt", "")
        junit = self._write("e.xml", "<testsuites><testsuite/></testsuites>")
        self.assertEqual(known_failures.main(["--junit", str(junit), "--baseline", str(baseline)]), 2)

    def test_too_few_tests_is_a_crash(self) -> None:
        baseline = self._write("b.txt", "core.tests.mod.ATests.test_a\npolicy.tests.mod.BTests.test_b\n"
                               "core.tests.mod.ATests.test_c\n")
        log = self._write("log.txt", _LOG)
        args = ["--log", str(log), "--baseline", str(baseline)]
        self.assertEqual(known_failures.main([*args, "--min-tests", "3"]), 0)
        self.assertEqual(known_failures.main([*args, "--min-tests", "500"]), 2)

    def test_unreadable_input_is_an_error(self) -> None:
        baseline = self._write("b.txt", "")
        self.assertEqual(
            known_failures.main(["--junit", str(self.dir / "missing.xml"), "--baseline", str(baseline)]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
