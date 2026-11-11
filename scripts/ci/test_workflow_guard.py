"""Self-tests for workflow_guard.py (run by the CI workflow's ci-scripts job)."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import workflow_guard

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
DIGEST = "sha256:" + "0123456789abcdef" * 4
REPO_ROOT = Path(__file__).resolve().parents[2]

GOOD = f"""\
name: good
on: [pull_request]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    container:
      image: python:3.12-slim@{DIGEST}
    services:
      redis:
        image: redis:7.4-alpine@{DIGEST}
    steps:
      - uses: actions/checkout@{SHA} # v7.0.1
        with:
          persist-credentials: false
      - name: script text is not a key
        run: |
          echo "uses: actions/checkout@v4"
          cat <<'YAML'
          permissions: write-all
          YAML
      - uses: ./.github/actions/local
      - uses: "docker://alpine:3.20@{DIGEST}"
  call:
    uses: ./.github/workflows/reusable-x.yml
  call-self:
    uses: $/.github/workflows/reusable-y.yml
  remote:
    uses: octo/repo/.github/workflows/x.yml@{SHA} # v1.0.0
"""


class WorkflowGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, text: str, name: str = "wf.yml") -> list[tuple[str, int]]:
        path = self.dir / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return [(f.rule, f.line) for f in workflow_guard.check_file(path).errors]

    def test_pinned_workflow_passes(self) -> None:
        self.assertEqual(self.check(GOOD), [])

    def test_counts_only_real_uses_keys(self) -> None:
        path = self.dir / "wf.yml"
        path.write_text(GOOD, encoding="utf-8")
        self.assertEqual(workflow_guard.check_file(path).uses, 6)

    def test_tag_pin_fails(self) -> None:
        self.assertEqual(self.check(GOOD.replace(f"actions/checkout@{SHA} # v7.0.1", "actions/checkout@v4")),
                         [("unpinned-uses", 17)])

    def test_short_sha_and_branch_fail(self) -> None:
        for ref in (SHA[:12], "main", ""):
            with self.subTest(ref=ref):
                bad = GOOD.replace(f"actions/checkout@{SHA}", f"actions/checkout@{ref}" if ref else "actions/checkout")
                self.assertEqual(self.check(bad), [("unpinned-uses", 17)])

    def test_sha_without_version_comment_fails(self) -> None:
        self.assertEqual(self.check(GOOD.replace(f"@{SHA} # v7.0.1", f"@{SHA}")), [("version-comment", 17)])
        self.assertEqual(self.check(GOOD.replace(f"@{SHA} # v7.0.1", f"@{SHA} # latest")),
                         [("version-comment", 17)])

    def test_remote_reusable_workflow_tag_fails(self) -> None:
        bad = GOOD.replace(f"x.yml@{SHA} # v1.0.0", "x.yml@v1")
        self.assertEqual(self.check(bad), [("unpinned-uses", 33)])

    def test_docker_uses_needs_digest(self) -> None:
        bad = GOOD.replace(f'"docker://alpine:3.20@{DIGEST}"', "docker://alpine:3.20")
        self.assertEqual(self.check(bad), [("unpinned-uses", 27)])

    def test_flow_style_and_next_line_uses_fail_closed(self) -> None:
        flow = GOOD.replace("      - uses: ./.github/actions/local", "      - { uses: actions/checkout@v4 }")
        self.assertIn(("unpinned-uses", 26), self.check(flow))
        next_line = GOOD.replace("      - uses: ./.github/actions/local",
                                 "      - uses:\n          actions/checkout@v4")
        self.assertIn(("unpinned-uses", 26), self.check(next_line))

    def test_anchored_and_quoted_uses_are_checked(self) -> None:
        anchored = GOOD.replace(f"      - uses: actions/checkout@{SHA} # v7.0.1", "      - &co uses: actions/checkout@v4")
        self.assertEqual(self.check(anchored), [("unpinned-uses", 17)])
        quoted = GOOD.replace(f"      - uses: actions/checkout@{SHA} # v7.0.1", "      - 'uses': 'actions/checkout@v4'")
        self.assertEqual(self.check(quoted), [("unpinned-uses", 17)])
        alias = GOOD.replace(f"      - uses: actions/checkout@{SHA} # v7.0.1", "      - uses: *checkout")
        self.assertEqual(self.check(alias), [("unpinned-uses", 17)])

    def test_missing_top_level_permissions_fails(self) -> None:
        bad = GOOD.replace("permissions:\n  contents: read\njobs:", "jobs:")
        self.assertEqual(self.check(bad), [("permissions", 1)])

    def test_job_level_permissions_do_not_count(self) -> None:
        bad = textwrap.dedent(f"""\
            on: push
            jobs:
              a:
                permissions:
                  contents: read
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@{SHA} # v7.0.1
            """)
        self.assertEqual(self.check(bad), [("permissions", 1)])

    def test_top_level_write_fails(self) -> None:
        for block, line in (
            ("permissions:\n  contents: read\n  pull-requests: write\n", 5),
            ("permissions: write-all\n", 3),
            ("permissions: read-all\n", 3),
            ("permissions: { contents: read, issues: write }\n", 3),
        ):
            with self.subTest(block=block):
                bad = GOOD.replace("permissions:\n  contents: read\njobs:", block + "jobs:")
                self.assertEqual(self.check(bad), [("top-level-write", line)])

    def test_empty_top_level_permissions_passes(self) -> None:
        empty = GOOD.replace("permissions:\n  contents: read\njobs:", "permissions: {}\njobs:")
        self.assertEqual(self.check(empty), [])

    def test_job_level_write_is_allowed(self) -> None:
        self.assertEqual(self.check(GOOD), [])  # GOOD grants security-events: write on its job

    def test_unpinned_images_fail(self) -> None:
        container = GOOD.replace(f"python:3.12-slim@{DIGEST}", "python:3.12-slim")
        self.assertEqual(self.check(container), [("unpinned-image", 12)])
        service = GOOD.replace(f"redis:7.4-alpine@{DIGEST}", "redis:7.4-alpine")
        self.assertEqual(self.check(service), [("unpinned-image", 15)])
        mapping = f"    container:\n      image: python:3.12-slim@{DIGEST}\n"
        scalar = GOOD.replace(mapping, "    container: ubuntu:24.04\n")
        self.assertEqual(self.check(scalar), [("unpinned-image", 11)])

    def test_expression_image_is_a_notice(self) -> None:
        path = self.dir / "wf.yml"
        path.write_text(GOOD.replace(f"redis:7.4-alpine@{DIGEST}", "${{ inputs.redis-image }}"), encoding="utf-8")
        result = workflow_guard.check_file(path)
        self.assertEqual(result.errors, [])
        self.assertEqual([(n.rule, n.line) for n in result.notices], [("unpinned-image", 15)])

    def test_composite_action_needs_no_permissions(self) -> None:
        action = f"""\
            name: local
            runs:
              using: composite
              steps:
                - uses: actions/setup-python@{SHA} # v7.1.0
            """
        self.assertEqual(self.check(action, name="action.yml"), [])
        self.assertEqual(self.check(action.replace(f"@{SHA} # v7.1.0", "@v5"), name="action.yml"),
                         [("unpinned-uses", 5)])

    def test_main_exit_codes(self) -> None:
        good = self.dir / "good.yml"
        good.write_text(GOOD, encoding="utf-8")
        bad = self.dir / "bad.yml"
        bad.write_text(GOOD.replace(f"@{SHA} # v7.0.1", "@v4"), encoding="utf-8")
        # The output format depends on GITHUB_ACTIONS, which is "true" on a runner: pin it.
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workflow_guard.main([str(good)]), 0)
            self.assertEqual(workflow_guard.main([str(self.dir)]), 1)
            self.assertEqual(workflow_guard.main([str(self.dir / "missing")]), 2)
        self.assertIn("bad.yml:17: error [unpinned-uses]", out.getvalue())
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workflow_guard.main([str(self.dir)]), 1)
        self.assertIn(f"::error file={bad},line=17,title=workflow guard (unpinned-uses)::", out.getvalue())
        self.assertNotIn("bad.yml:17: error", out.getvalue())

    def test_repository_workflows_pass(self) -> None:
        """The guard itself: every workflow in this repository is pinned and least-privilege."""
        workflows = REPO_ROOT / ".github" / "workflows"
        errors = [e for f in workflow_guard.collect([str(workflows)]) for e in workflow_guard.check_file(f).errors]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
