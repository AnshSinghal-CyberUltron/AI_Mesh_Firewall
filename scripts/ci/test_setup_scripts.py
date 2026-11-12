"""Self-tests for .github/ci/required-checks.txt, setup_rulesets.sh and setup_environments.sh.

Stdlib only (`CI helper scripts` runs them). The shell scripts run against an offline `gh`
stub first on PATH (`_Stub`). It never reaches GitHub: it answers `gh api` reads from canned
routes, applies `--jq` with the `jq` binary, and answers like real `gh` on an HTTP error (the
error JSON on stdout, exit 1). It refuses every write the same way and records it, with the body
sent, so the tests prove which write a run would make and that a run aborts before writing.

The workflow reader below is line-based on purpose (no PyYAML in this job): it relies on the
2-space indentation every workflow here uses, and fails loudly when it cannot find a job.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
# Added by PR #137 together with the `gateway-v2 pytest` job.
SHARD_PLUGIN = ROOT / "scripts" / "ci" / "amf_pytest_shards.py"
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
            if check == "gateway-v2 pytest" and not SHARD_PLUGIN.exists():
                continue  # PR #137 adds the job with the plugin; from then on it must exist
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


REPO = "CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall"
ORG = REPO.split("/")[0]
DEVELOP_SHA = "d" * 40
PR_SHA = "e" * 40
NEW_ID = 555

# The offline `gh`. Routes: [regex over the API path, HTTP status, JSON body].
_GH_STUB = r"""#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys

args = sys.argv[1:]
here = os.environ["STUB_DIR"]
with open(os.path.join(here, "calls"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")


def http_error(status, message):
    # What gh does on an HTTP error: the error JSON on stdout (--jq skipped), a line on stderr.
    print(json.dumps({"message": message, "status": str(status)}))
    print(f"gh: {message} (HTTP {status})", file=sys.stderr)
    sys.exit(1)


if not args or args[0] != "api":
    print("stub: only `gh api`", file=sys.stderr)
    sys.exit(98)
method, path, jq, silent, body_file, i = "GET", None, None, False, None, 1
while i < len(args):
    a = args[i]
    if a in ("-X", "--method"):
        method, i = args[i + 1], i + 2
    elif a == "--jq":
        jq, i = args[i + 1], i + 2
    elif a in ("--input", "-f", "-F", "--field", "--raw-field"):
        if a == "--input":
            body_file = args[i + 1]
        method, i = ("POST" if method == "GET" else method), i + 2
    elif a == "--silent":
        silent, i = True, i + 1
    elif a.startswith("-"):
        i += 1
    else:
        path, i = a, i + 1
if method != "GET":
    with open(os.path.join(here, "writes"), "a", encoding="utf-8") as fh:
        fh.write(f"{method} {path}\n")
    if body_file:
        shutil.copy(body_file, os.path.join(here, "write-body.json"))
    http_error(403, "stub: writes are refused")
with open(os.path.join(here, "routes.json"), encoding="utf-8") as fh:
    routes = json.load(fh)
for pattern, status, body in routes:
    if re.fullmatch(pattern, path):
        break
else:
    http_error(404, "Not Found")
if status >= 400:
    http_error(status, str(body))
if silent:
    sys.exit(0)
text = json.dumps(body)
if jq:
    out = subprocess.run(["jq", "-r", jq], input=text, capture_output=True, text=True)
    if out.returncode:
        print(out.stderr, file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(out.stdout)
else:
    print(text)
"""


def _route(path: str, body: Any, status: int = 200) -> list[Any]:
    return [re.escape(path), status, body]


def _check_runs(states: dict[str, str]) -> dict[str, Any]:
    return {
        "total_count": len(states),
        "check_runs": [
            {
                "id": i + 1,
                "name": name,
                "status": "completed",
                "conclusion": state,
                "app": {"id": 15368},
            }
            for i, (name, state) in enumerate(states.items())
        ],
    }


class _Stub:
    """The offline `gh` above, first on PATH, with `routes` as its canned answers."""

    def __init__(self, routes: list[list[Any]] | None = None) -> None:
        self.routes = routes or []

    def __enter__(self) -> _Stub:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)
        (self.path / "routes.json").write_text(json.dumps(self.routes))
        stub = self.path / "gh"
        stub.write_text(_GH_STUB)
        stub.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.dir.name}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": self.dir.name,
        }
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
        log = self.path / "calls"
        return log.read_text() if log.exists() else ""

    def writes(self) -> str:
        log = self.path / "writes"
        return log.read_text() if log.exists() else ""

    def write_body(self) -> dict[str, Any]:
        return dict(json.loads((self.path / "write-body.json").read_text()))

    def __exit__(self, *exc: object) -> None:
        self.dir.cleanup()


def _dry_body(*args: str) -> dict[str, Any]:
    with _Stub() as gh:
        proc = gh.run(RULESETS, "--dry-run", *args)
        assert proc.returncode == 0, proc.stderr
        return dict(json.loads(proc.stdout))


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
            body["conditions"],
            {
                "ref_name": {
                    "include": ["refs/heads/develop", "refs/heads/staging", "refs/heads/main"],
                    "exclude": [],
                }
            },
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

    def test_org_scope_targets_this_repository_only(self) -> None:
        body = self._body("--org")
        self.assertEqual(
            body["conditions"]["repository_name"],
            {"include": ["AI_Mesh_Firewall"], "exclude": [], "protected": True},
        )
        self.assertEqual(body["rules"], self._body()["rules"])
        with _Stub() as gh:
            proc = gh.run(RULESETS, "--dry-run", "--org", "--merge-queue")
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not available in organisation rulesets", proc.stderr)

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
                ["--pr-head", "NOT-A-SHA"],
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
        self.assertNotRegex(text, r"-X (PUT|PATCH|DELETE) \"[^\"]*rulesets/19609105")
        self.assertNotRegex(text, r"-X (PATCH|DELETE)")


@unittest.skipUnless(shutil.which("jq"), "the gh stub needs jq for --jq")
class RulesetsApplyTest(unittest.TestCase):
    """Apply and --check against canned GitHub answers. Every write is refused by the stub."""

    def _checks(self, red: str | None = None, missing: str | None = None) -> dict[str, Any]:
        states = {c: ("failure" if c == red else "success") for c in required_checks()}
        states.pop(missing or "", None)
        return _check_runs(states)

    def _routes(
        self,
        *,
        develop: Any = None,
        pr: Any = None,
        rulesets: Any = None,
        scope: str = f"repos/{REPO}",
        live: dict[str, Any] | None = None,
        branch_rules: dict[str, Any] | None = None,
    ) -> list[list[Any]]:
        query = (
            "per_page=100" if scope.startswith("orgs/") else "includes_parents=false&per_page=100"
        )
        routes = [
            _route(f"repos/{REPO}/branches/{b}", {"name": b, "commit": {"sha": DEVELOP_SHA}})
            for b in ("develop", "staging", "main")
        ]
        routes += [
            _route(
                f"repos/{REPO}/commits/{DEVELOP_SHA}/check-runs?per_page=100",
                develop if develop is not None else self._checks(),
            ),
            _route(
                f"repos/{REPO}/commits/{PR_SHA}/check-runs?per_page=100",
                pr if pr is not None else self._checks(),
            ),
            _route(f"{scope}/rulesets?{query}", rulesets if rulesets is not None else []),
            _route(
                f"repos/{REPO}/rulesets/19609105",
                {
                    "id": 19609105,
                    "enforcement": "active",
                    "rules": [
                        {"type": t}
                        for t in (
                            "deletion",
                            "non_fast_forward",
                            "creation",
                            "update",
                            "pull_request",
                        )
                    ],
                },
            ),
        ]
        if live is not None:
            routes.append(_route(f"{scope}/rulesets/{live['id']}", live))
        for branch, rules in (branch_rules or {}).items():
            routes.append(_route(f"repos/{REPO}/rules/branches/{branch}?per_page=100", rules))
        return routes

    def _named(self, rid: int, source: str = "Repository") -> list[dict[str, Any]]:
        return [
            {
                "id": rid,
                "name": "AMF protected branches: develop, staging, main",
                "source_type": source,
            }
        ]

    def _live(self, *args: str, **changes: Any) -> dict[str, Any]:
        live = {**_dry_body(*args), "id": NEW_ID, "source_type": "Repository", **changes}
        return live

    def _branch_rules(self, rid: int = NEW_ID) -> dict[str, Any]:
        core = ("deletion", "non_fast_forward", "pull_request", "required_status_checks")
        rules = {
            b: [{"type": t, "ruleset_id": rid} for t in core]
            for b in ("develop", "staging", "main")
        }
        rules["main"] += [{"type": t, "ruleset_id": 19609105} for t in ("creation", "update")]
        return rules

    def _apply(
        self, routes: list[list[Any]], *args: str
    ) -> tuple[subprocess.CompletedProcess[str], _Stub]:
        gh = _Stub(routes)
        with gh:
            proc = gh.run(RULESETS, *args)
            gh.result = (
                proc,
                gh.writes(),
                gh.calls(),
                gh.write_body() if (gh.path / "write-body.json").exists() else None,
            )
        return proc, gh

    def test_apply_needs_a_pr_head(self) -> None:
        proc, gh = self._apply(self._routes())
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("pass --pr-head", proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_unreadable_develop_check_runs_abort(self) -> None:
        # A NIT 5: gh's error JSON used to crash the parser, and the preflight then passed.
        routes = self._routes(develop=None)
        routes[3] = [routes[3][0], 403, "Resource not accessible by personal access token"]
        proc, gh = self._apply(routes, "--pr-head", PR_SHA)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("could not read the check runs on develop's head", proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_red_check_on_develop_aborts(self) -> None:
        proc, gh = self._apply(
            self._routes(develop=self._checks(red="CodeQL / analyze (python)")),
            "--pr-head",
            PR_SHA,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("red on develop's head", proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_check_missing_on_the_pr_head_aborts(self) -> None:
        # B N2: a required name that never reports would leave every PR waiting.
        proc, gh = self._apply(
            self._routes(pr=self._checks(missing="gateway-v2 pytest")), "--pr-head", PR_SHA
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("MISSING      gateway-v2 pytest", proc.stderr)
        self.assertIn("ABORT: not every required check succeeded", proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_refuses_to_write_19609105(self) -> None:
        proc, gh = self._apply(self._routes(rulesets=self._named(19609105)), "--pr-head", PR_SHA)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("refusing to modify ruleset 19609105", proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_creates_with_the_dry_run_body(self) -> None:
        proc, gh = self._apply(self._routes(), "--pr-head", PR_SHA)
        self.assertEqual(gh.result[1], f"POST repos/{REPO}/rulesets\n")
        self.assertEqual(gh.result[3], _dry_body())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ABORT: GitHub refused the write", proc.stderr)

    def test_updates_the_existing_ruleset_by_name(self) -> None:
        proc, gh = self._apply(self._routes(rulesets=self._named(55555)), "--pr-head", PR_SHA)
        self.assertEqual(gh.result[1], f"PUT repos/{REPO}/rulesets/55555\n")

    def test_org_scope_writes_to_the_organisation(self) -> None:
        proc, gh = self._apply(self._routes(scope=f"orgs/{ORG}"), "--org", "--pr-head", PR_SHA)
        self.assertEqual(gh.result[1], f"POST orgs/{ORG}/rulesets\n")
        self.assertEqual(gh.result[3], _dry_body("--org"))

    def test_two_rulesets_with_the_name_abort(self) -> None:
        proc, gh = self._apply(
            self._routes(rulesets=self._named(1) + self._named(2)), "--pr-head", PR_SHA
        )
        self.assertEqual(proc.returncode, 4, proc.stderr)
        self.assertEqual(gh.result[1], "")

    def test_check_ok_when_live_and_in_force(self) -> None:
        proc, gh = self._apply(
            self._routes(
                rulesets=self._named(NEW_ID), live=self._live(), branch_rules=self._branch_rules()
            ),
            "--check",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK: 0 difference(s)", proc.stdout)
        self.assertIn("main: also in force, ruleset 19609105: creation, update", proc.stdout)
        self.assertIn("19609105 'Main Protected by Ansh' still applies to main", proc.stderr)
        self.assertEqual(gh.result[1], "")
        self.assertNotRegex(gh.result[2], r"(?m)^api -X")

    def test_check_reports_every_difference(self) -> None:
        live = self._live(
            enforcement="evaluate",
            bypass_actors=[{"actor_type": "OrganizationAdmin", "actor_id": 1}],
        )
        status = next(r for r in live["rules"] if r["type"] == "required_status_checks")
        status["parameters"]["required_status_checks"].pop()
        rules = self._branch_rules()
        rules["main"] = [r for r in rules["main"] if r["type"] != "required_status_checks"]
        proc, gh = self._apply(
            self._routes(rulesets=self._named(NEW_ID), live=live, branch_rules=rules), "--check"
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        for fragment in (
            "FAIL  enforcement evaluate (want active)",
            "FAIL  bypass actors: 1 (want 0)",
            "FAIL  required_status_checks: 16 required checks",
            "FAIL  main: in force from ruleset 555",
        ):
            self.assertIn(fragment, proc.stdout)

    def test_check_without_the_ruleset_fails(self) -> None:
        proc, gh = self._apply(self._routes(), "--check")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("FAIL  no Repository ruleset named", proc.stdout)

    def test_check_unreadable_is_not_ok(self) -> None:
        routes = self._routes()
        routes[5] = [routes[5][0], 403, "Must have admin rights to Repository."]
        proc, gh = self._apply(routes, "--check")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("cannot list the rulesets", proc.stderr)

    def test_verify_sha_reading_error_is_not_success(self) -> None:
        routes = self._routes()
        routes[4] = [routes[4][0], 404, "Not Found"]
        proc, gh = self._apply(routes, "--verify-sha", PR_SHA)
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("cannot read the check runs", proc.stderr)


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
        self.assertIn("applying refuses a production user reviewer", proc.stderr)

    def test_names_unresolved_offline_are_placeholders(self) -> None:
        # A S1: the stub answers like gh on an HTTP error (error JSON on stdout, exit 1). That
        # JSON used to become the reviewer "id".
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
            self.assertEqual(gh.writes(), "")
        self.assertIn("<id of user octocat, resolved when applied>", proc.stdout)
        self.assertIn("<id of team release, resolved when applied>", proc.stdout)
        self.assertNotIn("message", proc.stdout)
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


@unittest.skipUnless(shutil.which("jq"), "the gh stub needs jq for --jq")
class EnvironmentsApplyTest(unittest.TestCase):
    """Apply mode against canned answers; the stub refuses every write."""

    ME = 11

    def _run(self, *args: str, me: Any = None) -> tuple[subprocess.CompletedProcess[str], str]:
        routes = [
            _route("users/octocat", {"login": "octocat", "id": 77}),
            _route("user", {"login": "owner", "id": self.ME} if me is None else me),
        ]
        with _Stub(routes) as gh:
            proc = gh.run(ENVIRONMENTS, *args)
            return proc, gh.writes()

    def test_unresolvable_reviewer_fails_before_any_write(self) -> None:
        # A S1: a mistyped login used to go into the PUT body as GitHub's error JSON.
        proc, writes = self._run(
            "--staging-reviewers", "user:octocat", "--production-reviewers", "user:no-such-login"
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("cannot resolve user 'no-such-login'", proc.stderr)
        self.assertEqual(writes, "")

    def test_own_account_as_production_reviewer_is_refused(self) -> None:
        # B S2: agents act as this account, so they could approve production deployments.
        proc, writes = self._run(
            "--staging-reviewers", f"user:#{self.ME}", "--production-reviewers", f"user:#{self.ME}"
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn(f"REFUSED: production reviewer user #{self.ME}", proc.stderr)
        self.assertEqual(writes, "")

    def test_override_warns_then_writes(self) -> None:
        proc, writes = self._run(
            "--staging-reviewers",
            f"user:#{self.ME}",
            "--production-reviewers",
            f"user:#{self.ME}",
            "--allow-own-account-in-production",
        )
        self.assertIn("WARNING: production reviewer", proc.stderr)
        self.assertEqual(writes, f"PUT repos/{REPO}/environments/staging\n")  # then refused

    def test_another_human_reviewer_passes_the_rule(self) -> None:
        proc, writes = self._run(
            "--staging-reviewers", f"user:#{self.ME}", "--production-reviewers", "user:octocat"
        )
        self.assertNotIn("REFUSED", proc.stderr)
        self.assertEqual(writes, f"PUT repos/{REPO}/environments/staging\n")

    def test_unreadable_own_account_fails_closed(self) -> None:
        proc, writes = self._run(
            "--staging-reviewers",
            "user:#1",
            "--production-reviewers",
            "user:#2",
            me={"message": "Bad credentials"},
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("cannot read the account running this script", proc.stderr)
        self.assertEqual(writes, "")


if __name__ == "__main__":
    unittest.main()
