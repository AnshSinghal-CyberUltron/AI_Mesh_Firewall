"""Self-tests for junit_timeouts.py (stdlib only; `CI helper scripts` runs them).

The JUnit fixtures are real pytest 9.2.0 + pytest-timeout 2.4.0 output, the versions the
openai-conformance job installs. HANG_B is Reviewer B's conf-hang reproduction for PR #137 S2
verbatim: a test that hangs in both runs must fail the LGW01 step, while ordinary recorded
failures (LEDGER) must leave it as it was.
"""

from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import junit_timeouts as jt  # noqa: E402

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/gateway-v2.yml"

# Reviewer B's conf-hang reproduction: timeout = 3, test_hangs sleeps 60 s.
HANG_B = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite '
    'name="pytest" errors="0" failures="1" skipped="0" tests="2" time="3.019" '
    'timestamp="2026-11-12T09:43:34.112509+00:00" hostname="vm"><testcase '
    'classname="tests.test_conf" name="test_ok" time="0.000" /><testcase '
    'classname="tests.test_conf" name="test_hangs" time="3.000"><failure message="Failed: '
    'Timeout (&gt;3.0s) from pytest-timeout.">E   Failed: Timeout (&gt;3.0s) from '
    "pytest-timeout.</failure></testcase></testsuite></testsuites>"
)

# Ordinary recorded outcomes: a pass, an assertion failure, a setup error, a skip, a quick xfail.
LEDGER = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite '
    'name="pytest" errors="1" failures="1" skipped="2" tests="5" time="0.047" '
    'timestamp="2026-11-12T10:09:38.487419+00:00" hostname="vm"><testcase '
    'classname="tests.test_ledger" name="test_ok" time="0.005" /><testcase '
    'classname="tests.test_ledger" name="test_ledger_assert" time="0.001"><failure '
    'message="assert 1 == 2">E   assert 1 == 2</failure></testcase><testcase '
    'classname="tests.test_ledger" name="test_ledger_setup_error" time="0.000"><error '
    'message="failed on setup with &quot;RuntimeError: recorded setup error&quot;">E   '
    "RuntimeError: recorded setup error</error></testcase><testcase "
    'classname="tests.test_ledger" name="test_skipped" time="0.003"><skipped '
    'type="pytest.skip" message="not here">tests/test_ledger.py:10: not here</skipped>'
    '</testcase><testcase classname="tests.test_ledger" name="test_known_defect" '
    'time="0.000"><skipped type="pytest.xfail" message="known defect" /></testcase>'
    "</testsuite></testsuites>"
)

# Every way a hang shows up, timeout = 3: in the call, in setup, in teardown, under a
# non-strict xfail (JUnit keeps only the reason), swallowed by `except BaseException` (passes),
# and under a lower per-test `@pytest.mark.timeout(1)`.
SHAPES = (
    '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite '
    'name="pytest" errors="2" failures="3" skipped="1" tests="8" time="16.031" '
    'timestamp="2026-11-12T10:04:52.344635+00:00" hostname="vm"><testcase '
    'classname="tests.test_shapes" name="test_ok" time="0.000" /><testcase '
    'classname="tests.test_shapes" name="test_ledger" time="0.001"><failure message="assert 1 '
    '== 2">E   assert 1 == 2</failure></testcase><testcase classname="tests.test_shapes" '
    'name="test_call_hang" time="3.000"><failure message="Failed: Timeout (&gt;3.0s) from '
    'pytest-timeout.">E   Failed: Timeout (&gt;3.0s) from pytest-timeout.</failure></testcase>'
    '<testcase classname="tests.test_shapes" name="test_setup_hang" time="3.000"><error '
    'message="failed on setup with &quot;Failed: Timeout (&gt;3.0s) from '
    'pytest-timeout.&quot;">E   Failed: Timeout (&gt;3.0s) from pytest-timeout.</error>'
    '</testcase><testcase classname="tests.test_shapes" name="test_teardown_hang" '
    'time="3.003"><error message="failed on teardown with &quot;Failed: Timeout (&gt;3.0s) '
    'from pytest-timeout.&quot;">E   Failed: Timeout (&gt;3.0s) from pytest-timeout.</error>'
    '</testcase><testcase classname="tests.test_shapes" name="test_xfail_hang" time="3.000">'
    '<skipped type="pytest.xfail" message="known defect" /></testcase><testcase '
    'classname="tests.test_shapes" name="test_swallowed_hang" time="3.000" /><testcase '
    'classname="tests.test_shapes" name="test_lowered_marker_hang" time="1.000"><failure '
    'message="Failed: Timeout (&gt;1.0s) from pytest-timeout.">E   Failed: Timeout (&gt;1.0s) '
    "from pytest-timeout.</failure></testcase></testsuite></testsuites>"
)
BY_MESSAGE = {"test_call_hang", "test_setup_hang", "test_teardown_hang", "test_lowered_marker_hang"}
BY_DURATION = {"test_xfail_hang", "test_swallowed_hang"}

GATEWAY_V2_INI = """[tool.pytest.ini_options]
timeout = {timeout}
timeout_method = "signal"
required_plugins = ["pytest-timeout>=2.3"]
{extra}
"""


def _case(name: str, seconds: str, body: str = "") -> str:
    return (
        f'<testsuites><testsuite><testcase classname="t" name="{name}" time="{seconds}">'
        f"{body}</testcase></testsuite></testsuites>"
    )


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PYTEST_TIMEOUT", None)

    def config(self, timeout: str = "3", extra: str = "", text: str | None = None) -> Path:
        path = self.dir / "pyproject.toml"
        path.write_text(
            text if text is not None else GATEWAY_V2_INI.format(timeout=timeout, extra=extra)
        )
        return path

    def junit(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text)
        return path

    def run_main(self, *args: object) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = jt.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def flagged(self, output: str) -> set[str]:
        return {m.group(1) for m in re.finditer(r"::(\w+): ", output)}


class StepTest(Base):
    """The two runs of the LGW01 step, as the workflow passes them."""

    def test_reviewer_b_hang_in_both_runs_fails(self) -> None:
        a, b = self.junit("gw01-a.xml", HANG_B), self.junit("gw01-b.xml", HANG_B)
        code, out, _ = self.run_main("--config", self.config(), a, b)
        self.assertEqual(code, 1)
        self.assertEqual(out.count("tests.test_conf::test_hangs: failure 'Timeout (>3.0s)"), 2)
        self.assertIn(f"FAIL {a}:", out)
        self.assertIn(f"FAIL {b}:", out)
        self.assertNotIn("test_ok", out)
        self.assertIn("2 test result(s) hit the per-test timeout", out)

    def test_reviewer_b_hang_in_one_run_fails(self) -> None:
        a, b = self.junit("gw01-a.xml", HANG_B), self.junit("gw01-b.xml", LEDGER)
        code, out, _ = self.run_main("--config", self.config(), a, b)
        self.assertEqual(code, 1)
        self.assertEqual(self.flagged(out), {"test_hangs"})

    def test_recorded_failures_unchanged(self) -> None:
        a, b = self.junit("gw01-a.xml", LEDGER), self.junit("gw01-b.xml", LEDGER)
        code, out, err = self.run_main("--config", self.config(), a, b)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out.strip(), "junit_timeouts_ok files=2 cases=10 limit=3s")

    def test_recorded_failures_unchanged_at_the_real_limit(self) -> None:
        a = self.junit("gw01-a.xml", LEDGER)
        code, out, _ = self.run_main("--config", self.config("300"), a)
        self.assertEqual(code, 0)
        self.assertIn("limit=300s", out)


class RulesTest(Base):
    def test_every_hang_shape_is_named(self) -> None:
        code, out, _ = self.run_main("--config", self.config(), self.junit("s.xml", SHAPES))
        self.assertEqual(code, 1)
        self.assertEqual(self.flagged(out), BY_MESSAGE | BY_DURATION)
        self.assertIn(
            "test_xfail_hang: ran 3.000 s against a per-test timeout of 3 s (reported xfail)", out
        )
        self.assertIn(
            "test_swallowed_hang: ran 3.000 s against a per-test timeout of 3 s (reported passed)",
            out,
        )
        self.assertIn("test_setup_hang: error 'Timeout (>3.0s) from pytest-timeout'", out)

    def test_message_rule_alone(self) -> None:
        # At 300 s the 3 s hangs are far below the duration rule: only the message names them.
        code, out, _ = self.run_main("--config", self.config("300"), self.junit("s.xml", SHAPES))
        self.assertEqual(code, 1)
        self.assertEqual(self.flagged(out), BY_MESSAGE)

    def test_no_timeout_configured_keeps_the_message_rule(self) -> None:
        cfg = self.config(text='[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        code, out, _ = self.run_main("--config", cfg, self.junit("s.xml", SHAPES))
        self.assertEqual(code, 1)
        self.assertEqual(self.flagged(out), BY_MESSAGE)
        code, out, _ = self.run_main("--config", cfg, self.junit("l.xml", LEDGER))
        self.assertEqual((code, out.strip()), (0, "junit_timeouts_ok files=1 cases=5 limit=off"))

    def test_zero_timeout_is_off(self) -> None:
        code, out, _ = self.run_main("--config", self.config("0"), self.junit("l.xml", LEDGER))
        self.assertEqual((code, out.strip()), (0, "junit_timeouts_ok files=1 cases=5 limit=off"))

    def test_env_overrides_the_ini_value(self) -> None:
        os.environ["PYTEST_TIMEOUT"] = "300"
        code, out, _ = self.run_main("--config", self.config("3"), self.junit("s.xml", SHAPES))
        self.assertEqual(code, 1)
        self.assertEqual(self.flagged(out), BY_MESSAGE)

    def test_slack(self) -> None:
        cases = {
            ("3", "2.999"): True,  # phases leave out the hook time between them
            ("3", "2.700"): True,
            ("3", "2.699"): False,
            ("300", "299.000"): True,  # slack is at most 1 s
            ("300", "298.999"): False,
            ("300", "0.200"): False,
            ("0.5", "0.450"): True,  # a tenth of a shorter limit
            ("0.5", "0.449"): False,
        }
        for (limit, seconds), hit in cases.items():
            with self.subTest(limit=limit, seconds=seconds):
                code, _, _ = self.run_main(
                    "--config", self.config(limit), self.junit("c.xml", _case("x", seconds))
                )
                self.assertEqual(code, 1 if hit else 0)

    def test_message_in_text_only_and_namespaced(self) -> None:
        body = "<error>E   Failed: Timeout (&gt;9.0s) from pytest-timeout.</error>"
        xml = _case("x", "0.1", body).replace("<testsuites>", '<testsuites xmlns="urn:x">')
        code, out, _ = self.run_main("--config", self.config("300"), self.junit("c.xml", xml))
        self.assertEqual(code, 1, out)

    def test_other_failure_messages_are_not_timeouts(self) -> None:
        body = '<failure message="Timeout talking to upstream">E   TimeoutError</failure>'
        code, _, _ = self.run_main(
            "--config", self.config("300"), self.junit("c.xml", _case("x", "1.0", body))
        )
        self.assertEqual(code, 0)


class CannotJudgeTest(Base):
    def assert_cannot_judge(self, *args: object, why: str) -> None:
        code, out, err = self.run_main(*args)
        self.assertEqual(code, 2, out + err)
        self.assertIn("cannot judge, failing", err)
        self.assertIn(why, err)

    def test_missing_junit(self) -> None:
        self.assert_cannot_judge(
            "--config", self.config(), self.dir / "absent.xml", why="absent.xml"
        )

    def test_doctype_and_entities(self) -> None:
        bomb = (
            '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;">]>'
            "<testsuites>&b;</testsuites>"
        )
        self.assert_cannot_judge(
            "--config", self.config(), self.junit("d.xml", bomb), why="DOCTYPE"
        )

    def test_malformed_xml(self) -> None:
        self.assert_cannot_judge(
            "--config",
            self.config(),
            self.junit("m.xml", "<testsuites><testcase"),
            why="not well-formed",
        )

    def test_bad_time(self) -> None:
        for seconds in ("abc", "nan", "inf", "-1", None):
            with self.subTest(seconds=seconds):
                xml = (
                    _case("x", "0").replace(' time="0"', "")
                    if seconds is None
                    else _case("x", seconds)
                )
                self.assert_cannot_judge(
                    "--config", self.config(), self.junit("t.xml", xml), why="t::x: time is"
                )

    def test_config_that_breaks_the_duration_rule(self) -> None:
        for extra, why in (
            ("timeout_func_only = true", "timeout_func_only"),
            ('timeout_func_only = "yes"', "timeout_func_only"),
            ('junit_duration_report = "call"', "junit_duration_report"),
        ):
            with self.subTest(extra=extra):
                self.assert_cannot_judge(
                    "--config", self.config(extra=extra), self.junit("l.xml", LEDGER), why=why
                )

    def test_bad_config(self) -> None:
        junit = self.junit("l.xml", LEDGER)
        self.assert_cannot_judge("--config", self.dir / "none.toml", junit, why="none.toml")
        self.assert_cannot_judge(
            "--config",
            self.config(text="[tool.ruff]\nline-length = 100\n"),
            junit,
            why="no [tool.pytest.ini_options]",
        )
        self.assert_cannot_judge(
            "--config", self.config(text="not = [toml"), junit, why="pyproject.toml"
        )
        self.assert_cannot_judge("--config", self.config('"soon"'), junit, why="not a number")

    def test_bad_env(self) -> None:
        junit = self.junit("l.xml", LEDGER)
        for value, why in (("abc", "not a number"), ("inf", "not finite"), ("", "not a number")):
            with self.subTest(value=value):
                os.environ["PYTEST_TIMEOUT"] = value
                self.assert_cannot_judge("--config", self.config(), junit, why=why)


class WorkflowWiringTest(unittest.TestCase):
    """The LGW01 step checks both JUnit files it writes, before compare_junit."""

    def step(self) -> list[str]:
        text = WORKFLOW.read_text(encoding="utf-8")
        start = text.index("- name: LGW01-1/2/4/5")
        end = re.compile(r"^\s+- name:", re.M).search(text, start + 1)
        block = text[start : end.start() if end else len(text)]
        joined = re.sub(r"\\\n\s*", "", block)
        return [line.strip() for line in joined.splitlines()]

    def index(self, lines: list[str], needle: str) -> int:
        found = [i for i, line in enumerate(lines) if needle in line]
        self.assertEqual(len(found), 1, f"{needle!r} in {found}")
        return found[0]

    def test_order(self) -> None:
        lines = self.step()
        rm = self.index(lines, "rm -f /tmp/gw01-a.xml /tmp/gw01-b.xml")
        run_a = self.index(lines, "--junitxml=/tmp/gw01-a.xml")
        run_b = self.index(lines, "--junitxml=/tmp/gw01-b.xml")
        present = self.index(lines, "test -s /tmp/gw01-a.xml && test -s /tmp/gw01-b.xml")
        check = self.index(
            lines,
            "../scripts/ci/junit_timeouts.py --config=pyproject.toml /tmp/gw01-a.xml "
            "/tmp/gw01-b.xml",
        )
        compare = self.index(lines, "lint.compare_junit /tmp/gw01-a.xml /tmp/gw01-b.xml")
        self.assertLess(rm, run_a)
        self.assertLess(run_a, run_b)
        self.assertLess(run_b, present)
        self.assertLess(present, check)
        self.assertLess(check, compare)
        self.assertEqual(lines[0], "- name: LGW01-1/2/4/5 Python ASGI + loopback TCP (twice)")
        self.assertIn("working-directory: gateway_v2", lines)


if __name__ == "__main__":
    unittest.main()
