"""Self-tests for .github/ci/required-checks.txt, setup_rulesets.sh and setup_environments.sh.

Stdlib only (`CI helper scripts` runs them). The shell scripts run in --dry-run mode with a
`gh` stub first on PATH that records any call and fails, so the tests prove dry runs make no
API call.

The workflow reader below is line-based on purpose (no PyYAML in this job): it relies on the
2-space indentation every workflow here uses, and fails loudly when it cannot find a job.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CHECKS_FILE = ROOT / ".github" / "ci" / "required-checks.txt"
RULESETS = ROOT / "scripts" / "ci" / "setup_rulesets.sh"
ENVIRONMENTS = ROOT / "scripts" / "ci" / "setup_environments.sh"
PROMOTION_BRANCHES = ("develop", "staging", "main")
# Job-level `if:` conditions that still run the job on every pull request.
RUNS_ON_EVERY_PR = {"github.event_name == 'pull_request'", "${{ always() }}", "always()"}


def required_checks() -> list[str]:
    return [
        line.strip()
        for line in CHECKS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _top_block(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() == f"{key}:":
            block = []
            for nxt in lines[i + 1 :]:
                if nxt and not nxt.startswith((" ", "#")):
                    break
                block.append(nxt)
            return block
    return []


def jobs(path: Path) -> dict[str, dict[str, str]]:
    """Job id -> {"name", "uses", "if", "matrix"} from a workflow's 2-space-indented `jobs:`."""
    result: dict[str, dict[str, str]] = {}
    current = None
    in_with = False
    for line in _top_block(path.read_text(encoding="utf-8"), "jobs"):
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            current = result.setdefault(m.group(1), {"name": m.group(1)})
            continue
        if current is None:
            continue
        if re.match(r"^    \S", line):
            in_with = line.startswith("    with:")  # a caller's inputs may override defaults
        elif in_with and (w := re.match(r"^      ([A-Za-z0-9_-]+):", line)):
            current["with"] = current.get("with", "") + f" {w.group(1)} "
        m = re.match(r"^    (name|uses|if):\s*(.+?)\s*$", line)
        if m:
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]  # a quoted scalar; inner quotes stay
            current[m.group(1)] = value
        elif re.match(r"^      matrix:", line):
            current["matrix"] = "yes"
        elif current.get("matrix") == "yes" and (
            m := re.match(r"^        [A-Za-z0-9_-]+:\s*(.+?)\s*$", line)
        ):
            current["matrix"] = m.group(
                1
            )  # the first matrix axis, e.g. `${{ fromJSON(inputs.x) }}`
    return result


def matrix_values(workflow: Path, axis: str) -> list[str] | None:
    """Values of a matrix axis written `${{ fromJSON(inputs.NAME) }}` (from the input's default)
    or as a flow list `[a, b]`; None when they cannot be known statically."""
    m = re.fullmatch(r"\$\{\{\s*fromJSON\(inputs\.([A-Za-z0-9_-]+)\)\s*\}\}", axis)
    if m:
        block = re.search(
            rf"^      {re.escape(m.group(1))}:\s*\n((?:        .*\n)+)",
            workflow.read_text(encoding="utf-8"),
            re.M,
        )
        default = block and re.search(r"^        default:\s*(.+?)\s*$", block.group(1), re.M)
        if not default:
            return None
        raw = default.group(1)
        if raw[0] == raw[-1] and raw[0] in "'\"":
            raw = raw[1:-1]
        return [str(v) for v in json.loads(raw)]
    if axis.startswith("[") and axis.endswith("]"):
        return [v.strip().strip("'\"") for v in axis[1:-1].split(",") if v.strip()]
    return None


def pr_trigger_covers_promotion_branches(path: Path) -> bool:
    """True when `on.pull_request` has no `paths` filter and its branches include all three."""
    text = path.read_text(encoding="utf-8")
    block = _top_block(text, "on")
    flat = "\n".join(block)
    if re.search(r"^on:\s*\[?.*pull_request", text, re.M) and not block:
        return True  # `on: [pull_request, ...]` or `on: pull_request`
    pr: list[str] = []
    inside = False
    for line in block:
        if re.match(r"^  pull_request:", line):
            inside = True
            pr.append(line)
            continue
        if inside and re.match(r"^  \S", line):
            break
        if inside:
            pr.append(line)
    if not pr:
        return False
    body = "\n".join(pr)
    if re.search(r"^\s+paths(-ignore)?:", body, re.M):
        return False
    if "branches:" not in body:
        return "pull_request" in flat
    return all(
        re.search(rf"(^|[\s\[,\"']){b}([\s\],\"']|$)", body, re.M) for b in PROMOTION_BRANCHES
    )


def check_names() -> dict[str, tuple[Path, dict[str, str]]]:
    """Every check-run name a PR can produce -> (workflow, job). Matrix names end in ' (*)'."""
    names: dict[str, tuple[Path, dict[str, str]]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for job in jobs(wf).values():
            uses = job.get("uses", "")
            if uses.startswith("./.github/workflows/"):
                callee = ROOT / uses[2:]
                for inner in jobs(callee).values():
                    base = f"{job['name']} / {inner['name']}"
                    entry = (wf, {**job, "inner_if": inner.get("if", "")})
                    axis = inner.get("matrix")
                    override = re.search(r"inputs\.([A-Za-z0-9_-]+)", axis or "")
                    overridden = override is not None and f" {override.group(1)} " in job.get(
                        "with", ""
                    )
                    values = matrix_values(callee, axis) if axis and not overridden else None
                    if axis and values is not None:
                        names.update({f"{base} ({v})": entry for v in values})
                    else:
                        names[base + (" (*)" if axis else "")] = entry
            else:
                name = job["name"] + (
                    " (*)" if job.get("matrix") and "${{" not in job["name"] else ""
                )
                names[name] = (wf, job)
    return names


def _match(
    required: str, names: dict[str, tuple[Path, dict[str, str]]]
) -> tuple[Path, dict[str, str]] | None:
    if required in names:
        return names[required]
    m = re.match(r"^(.*) \([^()]+\)$", required)
    if m and f"{m.group(1)} (*)" in names:
        return names[f"{m.group(1)} (*)"]
    return None


class RequiredChecksTest(unittest.TestCase):
    def test_reader_sees_the_workflows(self) -> None:
        names = check_names()
        self.assertIn("structural-gates", names)
        self.assertIn("control (Django + Postgres + Redis) / django", names)
        self.assertIn("CodeQL / analyze (python)", names)
        self.assertNotIn("CodeQL / analyze (rust)", names)

    def test_every_required_check_is_a_job_that_runs_on_every_pr(self) -> None:
        names = check_names()
        for check in required_checks():
            if check == "gateway-v2 pytest" and check not in names:
                continue  # added by PR #137; asserted below once that job exists
            hit = _match(check, names)
            if hit is None:
                self.fail(f"no workflow job produces the check {check!r}")
            wf, job = hit
            self.assertTrue(
                pr_trigger_covers_promotion_branches(wf),
                f"{check!r}: {wf.name} does not run on every PR into {PROMOTION_BRANCHES}",
            )
            for key in ("if", "inner_if"):
                cond = job.get(key, "")
                self.assertTrue(
                    not cond or cond in RUNS_ON_EVERY_PR,
                    f"{check!r}: job-level `if: {cond}` may skip it on a PR",
                )

    def test_aggregate_pytest_check_once_sharded(self) -> None:
        names = check_names()
        if any(n.startswith("gateway-v2 pytest shard") for n in names):
            self.assertIn("gateway-v2 pytest", names)
            self.assertEqual(names["gateway-v2 pytest"][1].get("if"), "${{ always() }}")

    def test_excluded_checks(self) -> None:
        checks = required_checks()
        self.assertNotIn("golden-freeze", checks)
        self.assertFalse([c for c in checks if "shard" in c])
        self.assertEqual(len(checks), len(set(checks)))
        self.assertFalse(
            pr_trigger_covers_promotion_branches(WORKFLOWS / "chat-pipeline-golden.yml")
        )


class _Stub:
    """A `gh` that records its arguments and fails, first on PATH."""

    def __enter__(self) -> _Stub:
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / "calls"
        stub = Path(self.dir.name) / "gh"
        stub.write_text(f'#!/bin/sh\necho "$*" >> "{self.log}"\nexit 1\n')
        stub.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.dir.name}{os.pathsep}{os.environ['PATH']}"}
        self.env.pop("AMF_REQUIRED_APPROVALS", None)
        return self

    def run(
        self, script: Path, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            env={**self.env, **(env or {})},
            timeout=60,
        )

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def __exit__(self, *exc: object) -> None:
        self.dir.cleanup()


class RulesetsDryRunTest(unittest.TestCase):
    def _body(self, *args: str, env: dict[str, str] | None = None) -> dict[str, Any]:
        with _Stub() as gh:
            proc = gh.run(RULESETS, "--dry-run", *args, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(gh.calls(), "", "dry run called gh")
            return dict(json.loads(proc.stdout))

    def _rule(self, body: dict[str, Any], kind: str) -> dict[str, Any]:
        rule = next((r for r in body["rules"] if r["type"] == kind), None)
        if rule is None:
            self.fail(f"no {kind} rule")
        return dict(rule)

    def test_defaults(self) -> None:
        body = self._body()
        self.assertEqual(body["target"], "branch")
        self.assertEqual(body["enforcement"], "active")
        self.assertEqual(body["bypass_actors"], [])
        self.assertEqual(
            body["conditions"]["ref_name"]["include"],
            ["refs/heads/develop", "refs/heads/staging", "refs/heads/main"],
        )
        self.assertEqual(
            [r["type"] for r in body["rules"]],
            ["deletion", "non_fast_forward", "pull_request", "required_status_checks"],
        )
        pr = self._rule(body, "pull_request")["parameters"]
        self.assertEqual(pr["required_approving_review_count"], 0)
        self.assertFalse(pr["require_last_push_approval"])
        self.assertEqual(pr["allowed_merge_methods"], ["merge"])
        checks = self._rule(body, "required_status_checks")["parameters"]
        self.assertTrue(checks["strict_required_status_checks_policy"])
        self.assertEqual(
            [c["context"] for c in checks["required_status_checks"]], required_checks()
        )
        self.assertEqual({c["integration_id"] for c in checks["required_status_checks"]}, {15368})

    def test_flags(self) -> None:
        body = self._body(
            "--copilot-review", "--merge-queue", "--without-codeql", "--no-strict", "--evaluate"
        )
        self.assertEqual(body["enforcement"], "evaluate")
        self.assertEqual(
            self._rule(body, "copilot_code_review")["parameters"],
            {"review_on_push": True, "review_draft_pull_requests": False},
        )
        queue = self._rule(body, "merge_queue")["parameters"]
        self.assertEqual(queue["merge_method"], "MERGE")
        self.assertEqual(
            set(queue),
            {
                "check_response_timeout_minutes",
                "grouping_strategy",
                "max_entries_to_build",
                "max_entries_to_merge",
                "merge_method",
                "min_entries_to_merge",
                "min_entries_to_merge_wait_minutes",
            },
        )
        checks = self._rule(body, "required_status_checks")["parameters"]
        self.assertFalse(checks["strict_required_status_checks_policy"])
        contexts = [c["context"] for c in checks["required_status_checks"]]
        self.assertFalse([c for c in contexts if c.startswith("CodeQL")])
        self.assertEqual(
            len(contexts), len([c for c in required_checks() if not c.startswith("CodeQL")])
        )

    def test_approvals_from_variable_and_flag(self) -> None:
        pr = self._rule(self._body(env={"AMF_REQUIRED_APPROVALS": "2"}), "pull_request")[
            "parameters"
        ]
        self.assertEqual(pr["required_approving_review_count"], 2)
        self.assertTrue(pr["require_last_push_approval"])
        pr = self._rule(self._body("--approvals", "1"), "pull_request")["parameters"]
        self.assertEqual(pr["required_approving_review_count"], 1)

    def test_rejects_bad_input(self) -> None:
        with _Stub() as gh:
            for args in (
                ["--approvals", "x"],
                ["--approvals", "11"],
                ["--repo", "noslash"],
                ["--bogus"],
            ):
                proc = gh.run(RULESETS, "--dry-run", *args)
                self.assertEqual(proc.returncode, 2, args)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
                fh.write("a\nb\na\n")
            try:
                proc = gh.run(RULESETS, "--dry-run", "--checks-file", fh.name)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("duplicates", proc.stderr)
            finally:
                os.unlink(fh.name)
            self.assertEqual(gh.calls(), "")

    def test_never_writes_the_main_ruleset(self) -> None:
        text = RULESETS.read_text(encoding="utf-8")
        self.assertIn('protected_ruleset_ids=" 19609105 "', text)
        self.assertNotRegex(text, r"-X (PUT|PATCH|DELETE) \"repos/\$repo/rulesets/19609105")


class EnvironmentsDryRunTest(unittest.TestCase):
    def test_dry_run_requests(self) -> None:
        with _Stub() as gh:
            proc = gh.run(
                ENVIRONMENTS,
                "--dry-run",
                "--staging-reviewers",
                "user:#11",
                "--production-reviewers",
                "user:#11,team:#22",
                "--production-wait-minutes",
                "10",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(gh.calls(), "", "ids given as #N need no lookup")
        lines = proc.stdout.splitlines()
        def body_after(suffix: str) -> dict[str, Any]:
            index = next(i for i, ln in enumerate(lines) if ln.endswith(suffix))
            return dict(json.loads(lines[index + 1]))

        staging = body_after("/environments/staging")
        production = body_after("/environments/production")
        self.assertEqual(staging["reviewers"], [{"type": "User", "id": 11}])
        self.assertEqual(
            production["reviewers"], [{"type": "User", "id": 11}, {"type": "Team", "id": 22}]
        )
        self.assertEqual(production["wait_timer"], 10)
        for env in (staging, production):
            self.assertFalse(env["can_admins_bypass"])
            self.assertFalse(env["prevent_self_review"])
            self.assertEqual(
                env["deployment_branch_policy"],
                {"protected_branches": False, "custom_branch_policies": True},
            )
        self.assertIn('exactly [{"name":"staging","type":"branch"}]', proc.stdout)
        self.assertIn('exactly [{"name":"main","type":"branch"}]', proc.stdout)

    def test_names_unresolved_offline_are_placeholders(self) -> None:
        with _Stub() as gh:
            proc = gh.run(
                ENVIRONMENTS,
                "--dry-run",
                "--staging-reviewers",
                "user:octocat",
                "--production-reviewers",
                "team:release",
                "--staging-branch",
                "develop",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("users/octocat", gh.calls())  # a read, never a write
            self.assertNotRegex(gh.calls(), r"-X (PUT|POST|DELETE)")
        self.assertIn("<id of user octocat, resolved when applied>", proc.stdout)
        self.assertIn('exactly [{"name":"develop","type":"branch"}]', proc.stdout)

    def test_rejects_bad_input(self) -> None:
        many = ",".join(f"user:#{i}" for i in range(1, 8))
        cases = [
            ["--staging-reviewers", "user:#1"],
            ["--staging-reviewers", "user:#1", "--production-reviewers", many],
            ["--staging-reviewers", "bob", "--production-reviewers", "user:#1"],
            ["--staging-reviewers", "user:#x", "--production-reviewers", "user:#1"],
            [
                "--staging-reviewers",
                "user:#1",
                "--production-reviewers",
                "user:#1",
                "--production-wait-minutes",
                "-1",
            ],
            [
                "--staging-reviewers",
                "user:#1",
                "--production-reviewers",
                "user:#1",
                "--staging-branch",
                "a b",
            ],
        ]
        with _Stub() as gh:
            for args in cases:
                proc = gh.run(ENVIRONMENTS, "--dry-run", *args)
                self.assertNotEqual(proc.returncode, 0, args)


if __name__ == "__main__":
    unittest.main()
