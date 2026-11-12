"""Reviewer A's 26 YAML cases from the #114 review (S1), each a self-test of workflow_guard.py.

Each case hides `actions/checkout@v4` (an unpinned action) or a write/missing top-level permission
in a form PyYAML and actionlint accept. The guard must fail every case except 08_comment_only,
where `uses:` appears only in comments. Contents are byte-for-byte the reviewer's files
(written with escapes, so invisible characters such as U+2028 are visible here).
Run by the CI workflow's ci-scripts job.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_guard

PASSING = {"08_comment_only"}
REQUIRE_YAML = os.environ.get("WORKFLOW_GUARD_REQUIRE_YAML") == "1"

CASES: dict[str, str] = {
    '01_plain': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '02_anchor_on_key': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - &co uses: actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '03_alias_value': 'on: push\npermissions:\n  contents: read\nenv:\n  REF: &ref actions/checkout@v4\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: *ref\n',
    '04_anchor_steps_alias': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: &s\n      - uses: actions/checkout@v4\n        with:\n          bogus-input: x\n  b:\n    runs-on: ubuntu-latest\n    steps: *s\n',
    '05_flow_step': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - {uses: actions/checkout@v4, with: {bogus-input: x}}\n',
    '06_flow_multiline': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - {name: x,\n         uses: actions/checkout@v4}\n',
    '07_block_scalar_value': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: >-\n          actions/checkout@v4\n',
    '08_comment_only': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      # uses: actions/checkout@v4\n      - run: echo  # uses: actions/checkout@v4\n',
    '09_trailing_spaces': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4   \n        with:\n          bogus-input: x\n',
    '10_quoted_value': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: "actions/checkout@v4"   # v4\n        with:\n          bogus-input: x\n',
    '11_single_quoted_key': "on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - 'uses': actions/checkout@v4\n        with:\n          bogus-input: x\n",
    '12_job_level_reusable': 'on: push\npermissions:\n  contents: read\njobs:\n  call:\n    uses: octo-org/repo/.github/workflows/w.yml@main\n',
    '13_tag_value': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: !!str actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '14_key_space_colon': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses  : actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '15_value_next_line': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses:\n          actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '20_escaped_key_x': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - "use\\x73": actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '21_escaped_key_u': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - "\\u0075ses": actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '22_explicit_key': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - ? uses\n        : actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '23_flow_escaped_key': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - {"use\\x73": actions/checkout@v4, with: {bogus-input: x}}\n',
    '24_flow_explicit_key': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - {? uses : actions/checkout@v4, with: {bogus-input: x}}\n',
    '25_quoted_ml_fake_scalar': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - name: "x\n     y: |\n          "\n        run: echo hi\n      - uses: actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '26_u2028_fake_scalar': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - name: "x\u2028y: |\u2028   "\n        run: echo hi\n      - uses: actions/checkout@v4\n        with:\n          bogus-input: x\n',
    '30_multidoc_perm_doc2': 'on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n---\npermissions:\n  contents: read\n',
    '31_multidoc_uses_doc2': 'on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n---\non: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n',
    '40_perm_escaped_write': 'on: push\npermissions:\n  contents: "writ\\x65"\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n',
    '41_perm_tag_write': 'on: push\npermissions:\n  contents: !!str write\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n',
}


class ReviewCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_case(self, name: str, cross_check: bool) -> workflow_guard.Result:
        path = self.dir / f"{name}.yml"
        path.write_text(CASES[name], encoding="utf-8")
        return workflow_guard.check_file(path, cross_check=cross_check)

    def test_there_are_26_cases(self) -> None:
        self.assertEqual(len(CASES), 26)

    def test_line_scanner_alone_fails_every_bad_case(self) -> None:
        for name in CASES:
            with self.subTest(case=name):
                errors = self.run_case(name, cross_check=False).errors
                if name in PASSING:
                    self.assertEqual(errors, [])
                else:
                    self.assertNotEqual(errors, [], "the scanner alone passed a case PyYAML reads as unsafe")

    def test_with_parser_cross_check(self) -> None:
        if workflow_guard.yaml is None:
            if REQUIRE_YAML:
                self.fail("WORKFLOW_GUARD_REQUIRE_YAML=1 but PyYAML is not importable")
            self.skipTest("PyYAML not installed")
        for name in CASES:
            with self.subTest(case=name):
                result = self.run_case(name, cross_check=True)
                if name in PASSING:
                    self.assertEqual(result.errors, [])
                    self.assertTrue(result.cross_checked)
                else:
                    self.assertNotEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
