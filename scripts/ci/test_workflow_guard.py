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

    def make_local_targets(self, action_step: str = f"actions/setup-python@{SHA} # v7.1.0") -> None:
        """Create the local action and reusable workflows that GOOD references, under self.dir."""
        action = self.dir / ".github" / "actions" / "local" / "action.yml"
        action.parent.mkdir(parents=True)
        (self.dir / ".github" / "workflows").mkdir()
        action.write_text(f"name: local\nruns:\n  using: composite\n  steps:\n    - uses: {action_step}\n",
                          encoding="utf-8")
        for name in ("reusable-x.yml", "reusable-y.yml"):
            (self.dir / ".github" / "workflows" / name).write_text(
                "on: workflow_call\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: echo hi\n", encoding="utf-8")

    def run_main(self, *args: str) -> tuple[int, str]:
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), redirect_stdout(io.StringIO()) as out:
            code = workflow_guard.main([*args, "--root", str(self.dir)])
        return code, out.getvalue()

    def test_main_exit_codes(self) -> None:
        self.make_local_targets()
        good = self.dir / "good.yml"
        good.write_text(GOOD, encoding="utf-8")
        bad = self.dir / "bad.yml"
        bad.write_text(GOOD.replace(f"@{SHA} # v7.0.1", "@v4"), encoding="utf-8")
        # The output format depends on GITHUB_ACTIONS, which is "true" on a runner: pin it.
        root = ["--root", str(self.dir)]
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workflow_guard.main([str(good), *root]), 0)
            self.assertEqual(workflow_guard.main([str(self.dir), *root]), 1)
            self.assertEqual(workflow_guard.main([str(self.dir / "missing"), *root]), 2)
        self.assertIn("bad.yml:17: error [unpinned-uses]", out.getvalue())
        self.assertIn("(+3 local actions followed)", out.getvalue())
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workflow_guard.main([str(self.dir), *root]), 1)
        self.assertIn(f"::error file={bad},line=17,title=workflow guard (unpinned-uses)::", out.getvalue())
        self.assertNotIn("bad.yml:17: error", out.getvalue())

    # --- Review S1 (#114): YAML that a parser reads as an unpinned `uses:` must not pass -------

    def test_escaped_or_explicit_keys_fail_closed(self) -> None:
        step = f"      - uses: actions/checkout@{SHA} # v7.0.1"
        for form in (
            '      - "use\\x73": actions/checkout@v4',            # escaped double-quoted key
            '      - "u\\x73es": actions/checkout@v4',
            "      - ? uses\n        : actions/checkout@v4",       # explicit-key syntax
            '      - {"use\\x73": actions/checkout@v4}',            # flow form of the escaped key
            "      - {? uses : actions/checkout@v4}",              # flow form of the explicit key
        ):
            with self.subTest(form=form):
                rules = {rule for rule, _ in self.check(GOOD.replace(step, form))}
                self.assertIn("unverifiable", rules)

    def test_plain_quoted_key_is_checked_not_rejected(self) -> None:
        step = f"      - uses: actions/checkout@{SHA} # v7.0.1"
        self.assertEqual(self.check(GOOD.replace(step, '      - "uses": actions/checkout@v4')),
                         [("unpinned-uses", 17)])

    def test_multiline_quoted_scalar_cannot_hide_later_steps(self) -> None:
        # The continuation line looks like `y: |`; a naive scanner would skip the rest of the job.
        hidden = GOOD.replace("      - uses: ./.github/actions/local",
                              '      - name: "x\n          y: |\n          "\n      - uses: actions/checkout@v4')
        rules = self.check(hidden)
        self.assertIn(("unverifiable", 26), rules)
        self.assertIn(("unpinned-uses", 29), rules)
        single = GOOD.replace("      - name: script text is not a key", "      - name: 'it''s\n          y: |")
        self.assertIn(("unverifiable", 20), self.check(single))

    def test_unicode_line_separators_fail(self) -> None:
        for char in ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x00"):
            with self.subTest(char=hex(ord(char))):
                text = GOOD.replace("      - name: script text is not a key",
                                    f'      - name: "x{char}y: |"')
                self.assertIn(("unverifiable", 20), self.check(text))

    def test_multiline_flow_collection_fails(self) -> None:
        text = GOOD.replace("on: [pull_request]", "on: [pull_request,\n  push]")
        self.assertIn(("unverifiable", 2), self.check(text))
        one_line = GOOD.replace("on: [pull_request]", 'on: [pull_request, "push", {a: "b, c"}]')
        self.assertEqual(self.check(one_line), [])

    def test_second_document_fails(self) -> None:
        no_perms = GOOD.replace("permissions:\n  contents: read\njobs:", "jobs:")
        rules = self.check(no_perms + "---\npermissions:\n  contents: read\n")
        self.assertIn(("unverifiable", 32), rules)  # the second `---`
        self.assertEqual(self.check("---\n" + GOOD), [])  # a leading document marker is fine

    def test_top_level_permission_must_be_plain_read_or_none(self) -> None:
        for level in ('"writ\\x65"', "!!str write", "*alias", "'write'", "Read", ""):
            with self.subTest(level=level):
                text = GOOD.replace("permissions:\n  contents: read\njobs:",
                                    f"permissions:\n  contents: {level}\njobs:")
                rules = {rule for rule, line in self.check(text) if line == 4}
                self.assertTrue(rules & {"unverifiable", "top-level-write"}, rules)
        for level in ("read", "none", '"read"'):
            with self.subTest(level=level):
                text = GOOD.replace("permissions:\n  contents: read\njobs:",
                                    f"permissions:\n  contents: {level}\njobs:")
                self.assertEqual(self.check(text), [])
        flow = GOOD.replace("permissions:\n  contents: read\njobs:", 'permissions: { contents: "writ\\x65" }\njobs:')
        self.assertIn(("unverifiable", 3), self.check(flow))

    # --- Review S2 (#114): a local action cannot hide an unpinned `uses:` ----------------------

    def test_local_composite_action_is_followed(self) -> None:
        self.make_local_targets(action_step="actions/checkout@v4")
        (self.dir / "good.yml").write_text(GOOD, encoding="utf-8")
        code, out = self.run_main(str(self.dir / "good.yml"))
        self.assertEqual(code, 1, out)
        self.assertIn(".github/actions/local/action.yml:5: error [unpinned-uses] `actions/checkout@v4`", out)

    def test_missing_or_escaping_local_reference_fails(self) -> None:
        self.make_local_targets()
        for ref, expected in (("./.github/actions/nope", "has no workflow file or action.yml"),
                              ("./../outside", "points outside the repository")):
            with self.subTest(ref=ref):
                (self.dir / "good.yml").write_text(GOOD.replace("./.github/actions/local", ref), encoding="utf-8")
                code, out = self.run_main(str(self.dir / "good.yml"))
                self.assertEqual(code, 1, out)
                self.assertIn(f"good.yml:26: error [local-ref] `{ref}` {expected}", out)

    def test_docker_action_image_needs_digest(self) -> None:
        action = self.dir / "action.yml"
        action.write_text("name: d\nruns:\n  using: docker\n  image: docker://alpine:3.20\n", encoding="utf-8")
        self.assertEqual([(f.rule, f.line) for f in workflow_guard.check_file(action).errors],
                         [("unpinned-image", 4)])
        action.write_text("name: d\nruns:\n  using: docker\n  image: Dockerfile\n", encoding="utf-8")
        self.assertEqual(workflow_guard.check_file(action).errors, [])

    def test_repository_workflows_pass(self) -> None:
        """The guard itself: every workflow in this repository is pinned and least-privilege."""
        workflows = REPO_ROOT / ".github" / "workflows"
        errors = [e for f in workflow_guard.collect([str(workflows)]) for e in workflow_guard.check_file(f).errors]
        self.assertEqual(errors, [])
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(workflow_guard.main([str(workflows), "--root", str(REPO_ROOT)]), 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
