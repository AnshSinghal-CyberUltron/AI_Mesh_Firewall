"""Self-tests for the PR policy script embedded in .github/workflows/pr-policy.yml.

The policy runs inline (`python3 - <<'PY' ... PY`) in the workflow, so these tests extract that
exact script from the workflow file and run it with the environment the step sets. A change to
the workflow's script is therefore what gets tested. Run by the CI workflow's ci-scripts job.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-policy.yml"

GOOD_BODY = "## Summary\n\nWhy, what changed, and how it was checked, in enough words.\n\n## Tests\n\nunit tests\n"
DEPENDABOT_BODY = "Bumps the actions group with 1 update: actions/checkout.\n<details><summary>Release notes</summary></details>"


def policy_script() -> str:
    """Return the heredoc between `python3 - <<'PY'` and the closing `PY` line."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "python3 - <<'PY'")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "PY")
    return textwrap.dedent("\n".join(lines[start + 1 : end])) + "\n"


def run_policy(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    env.update({
        "BASE": "develop",
        "HEAD": "ci/example",
        "TITLE": "ci: example change",
        "BODY": GOOD_BODY,
        "ADDITIONS": "10",
        "DELETIONS": "2",
        "CHANGED_FILES": "1",
        "AUTHOR": "some-developer",
    })
    env.update(overrides)
    return subprocess.run([sys.executable, "-"], input=policy_script(), env=env,
                          capture_output=True, text=True, check=False)


class PrPolicyTests(unittest.TestCase):
    def assert_errors(self, result: subprocess.CompletedProcess[str], *fragments: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for fragment in fragments:
            self.assertIn(fragment, result.stdout)

    def test_script_is_extracted_from_the_workflow(self) -> None:
        script = policy_script()
        self.assertIn('os.environ.get("AUTHOR", "") == "dependabot[bot]"', script)
        self.assertIn("AUTHOR: ${{ github.event.pull_request.user.login }}", WORKFLOW.read_text(encoding="utf-8"))

    def test_human_pr_with_complete_description_passes(self) -> None:
        result = run_policy()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("dependabot", result.stdout)

    def test_human_pr_needs_description_and_testing_section(self) -> None:
        self.assert_errors(run_policy(BODY="fix"), "Description is too short", "testing section")
        self.assert_errors(run_policy(BODY=DEPENDABOT_BODY), "testing section")

    def test_dependabot_skips_only_the_description_rules(self) -> None:
        result = run_policy(AUTHOR="dependabot[bot]", HEAD="dependabot/github_actions/actions-abc",
                            TITLE="ci(deps): bump the actions group with 1 update", BODY=DEPENDABOT_BODY)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("description-length and testing-section rules skipped", result.stdout)
        empty = run_policy(AUTHOR="dependabot[bot]", HEAD="dependabot/pip/x", TITLE="ci(deps): bump x", BODY="")
        self.assertEqual(empty.returncode, 0, empty.stdout)

    def test_dependabot_title_rule_still_applies(self) -> None:
        result = run_policy(AUTHOR="dependabot[bot]", HEAD="dependabot/github_actions/actions-abc",
                            TITLE="Bump the actions group with 1 update", BODY=DEPENDABOT_BODY)
        self.assert_errors(result, "Title must follow Conventional Commits")
        self.assertNotIn("Description is too short", result.stdout)

    def test_dependabot_promotion_rules_still_apply(self) -> None:
        to_main = run_policy(AUTHOR="dependabot[bot]", BASE="main", HEAD="dependabot/github_actions/x",
                             TITLE="ci(deps): bump x", BODY=DEPENDABOT_BODY)
        self.assert_errors(to_main, "cannot be merged into 'main'", "Release notes")
        to_staging = run_policy(AUTHOR="dependabot[bot]", BASE="staging", HEAD="dependabot/github_actions/x",
                                TITLE="ci(deps): bump x", BODY=DEPENDABOT_BODY)
        self.assert_errors(to_staging, "cannot be merged into 'staging'")

    def test_exemption_needs_the_exact_author_login(self) -> None:
        for author in ("dependabot", "Dependabot[bot]", "dependabot[bot] ", "x-dependabot[bot]", ""):
            with self.subTest(author=author):
                result = run_policy(AUTHOR=author, TITLE="ci(deps): bump x", BODY=DEPENDABOT_BODY)
                self.assert_errors(result, "testing section")
                self.assertNotIn("rules skipped", result.stdout)

    def test_pr_text_cannot_claim_the_exemption(self) -> None:
        result = run_policy(TITLE="ci(deps): bump x", HEAD="dependabot/github_actions/x",
                            BODY="dependabot[bot] AUTHOR=dependabot[bot]")
        self.assert_errors(result, "Description is too short", "testing section")


if __name__ == "__main__":
    unittest.main()
