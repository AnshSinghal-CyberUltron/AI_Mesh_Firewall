# CI/CD pipelines

All checks run on GitHub Actions with free tools only: no accounts, API keys or paid apps. Each pipeline is assembled from **reusable workflows** (`.github/workflows/reusable-*.yml`), so another repo can call the same building blocks with one `uses:` line.

## Branch flow

```
feature/* fix/* ci/* docs/* chore/* refactor/* test/* perf/* dependabot/*
        │  PR (2 independent reviews + green checks)
        ▼
     develop ──PR──▶ staging ──PR──▶ main
                       ▲               ▲
                       └── hotfix/* ───┘
```

`pr-policy.yml` enforces this promotion path. (`dev` cannot be a branch name here, because `dev/perf-9stage` already exists, so the development branch is `develop`.)

## What runs, and what blocks

| Pipeline | Job | Tool | On a PR it… |
|---|---|---|---|
| `CI` (`ci.yml`) | control | Django tests, Postgres 16 + Redis 7.4 service containers | **blocks** on any failure not listed in `.github/ci/control-known-failures.txt`, and on a run that did not complete (no test summary, fewer than 500 tests, or a non-zero exit with no parsed failure) |
| | frontend | `npm ci`, `node --test` unit tests, `vite build` | **blocks** |
| | CI helper scripts | self-tests for `scripts/ci/known_failures.py` | **blocks** |
| `gateway-v2` (`gateway-v2.yml`) | structural-gates | size, HTTP, mutability, capacity, tenant-scale AST gates; import-linter; ruff; mypy --strict; console inventory; ~5,000 pytest | **blocks** |
| | secret-headers, image-digest, openai-conformance, parity-harness | repo-specific gates | **block** |
| `Security` (`security.yml`) | secrets | gitleaks (merge commits included, `--remerge-diff`) + TruffleHog (verified only) on the PR's commits, or the pushed range | **blocks**, also on a TruffleHog scan error; the weekly full-history gitleaks sweep blocks too |
| | SAST | Semgrep OSS, diff-aware: new ERROR findings against the merge base (PR) or the previous head (push) | **blocks** |
| | | Bandit (high severity, high confidence) | report → code scanning |
| | dependencies | dependency-review: new deps with high+ CVEs or AGPL/SSPL licences | **blocks** once the Dependency graph is enabled. Until then the probe tolerates a 403/404 that is not a rate limit (GitHub answers a bare `Forbidden` while the graph is off) and passes with a warning; any other error fails |
| | | OSV-Scanner on every lockfile, npm audit | report → code scanning |
| | infra lint | actionlint, hadolint (error), shellcheck (error), `docker compose config`, `nginx -t` | **blocks** |
| | | workflow guard (`scripts/ci/workflow_guard.py`): SHA-pinned `uses:` in every workflow and every local action they use, top-level least-privilege `permissions:`, digest-pinned job images; see [below](#workflow-guard-pinned-actions-and-least-privilege-tokens) | **blocks** |
| | | Trivy config (HIGH, CRITICAL) | report → code scanning |
| | CodeQL | Python, JavaScript/TypeScript, GitHub Actions; `security-extended` | alerts → code scanning |
| `PR policy` (`pr-policy.yml`) | policy | Conventional Commit title, description with a testing section, size report, promotion path, release notes into `main`. A PR whose author is exactly `dependabot[bot]` skips only the description-length and testing-section rules ([why](#dependabot-prs-and-the-pr-policy)) | **blocks** |
| `chat-pipeline-golden` | golden-freeze | v1 gateway golden tests; runs only when the v1 pipeline files, `docs/pipeline/`, the golden cases or this workflow change | **red since 6 Aug 2026 on every branch**; waived per `MERGE_POLICY.md` until fixed |
| Dependabot (`dependabot.yml`) | | weekly grouped update PRs into `develop` for Actions (SHA pins and their version comments, titled `ci(deps): …`), and pip, npm and Docker (titled `build(deps): …`) | |

The weekly `schedule` run of `Security` scans the **full git history** for secrets and every lockfile, so it catches anything the diff-aware PR checks cannot see. Full sweeps are **report-only**:
- Semgrep without a baseline, and TruffleHog over the whole history, upload SARIF and artifacts and annotate the run.
- They do not fail it, because a finding in old commits cannot be fixed by the run that finds it.

**Schedules and Dependabot run only from the default branch (`main`).** GitHub reads `schedule:` triggers and `.github/dependabot.yml` from the default branch. So the weekly sweep and the update PRs start once these files reach `main` through the promotion path.

### Why some scanners are report-only

The repo already carries findings: 319 OSV advisories, root containers flagged by Trivy, and legacy Bandit hits. A scanner that blocks on legacy findings keeps every PR red until people stop reading it. So:

- **Diff-aware scanners block.** Secrets on the PR's commits or pushed range, Semgrep `--baseline-commit`, and dependency-review only see what the change adds.
- **Whole-repo scanners report** to the Security tab.

Flip `osv-blocking`, `bandit-blocking`, `npm-audit-blocking` or `trivy-blocking` to `true` in `security.yml` once a scanner is at zero.

### Baselines (they may only shrink)

| File | What it holds | Rule |
|---|---|---|
| `.github/ci/control-known-failures.txt` | 23 control tests failing when the gate was introduced | delete a line when its test passes (the ratchet prints a notice); never add one |
| `.gitleaksignore` | fingerprints of findings in **old commits** (no secret values) | never add a fingerprint for a new commit; remove the secret instead |
| `.gitleaks.toml` | every rule off only in the detector test corpora, specs and evidence; other `tests/`/`fixtures/` skip `generic-api-key` only | never widen it to hide a real finding |

### Dependabot PRs and the PR policy

Dependabot writes its own PR body (release notes and a commit list), which can never carry a testing section, so without an exemption no update PR, and so no action-pin bump, could pass `policy`.

- **What is skipped.** When `github.event.pull_request.user.login` is exactly `dependabot[bot]`, `pr-policy.yml` skips the description-length and testing-section rules, and says so in the job summary.
  - The login belongs to GitHub's Dependabot app; no person or other app can register it.
  - The check reads the event's author, never the title, body or branch name, so PR text cannot claim the exemption.
- **What still applies.** The Conventional Commits title, the promotion path, release notes into `main`, and every other check. `dependabot.yml` titles Actions updates `ci(deps): …` and pip, npm and Docker updates `build(deps): …` / `build(deps-dev): …`, so they pass the title rule; Dependabot's default `Bump …` title would not. A Dependabot PR still needs two reviews under `MERGE_POLICY.md`; its tests are the CI checks.
- **A person who pushes to a Dependabot branch** keeps the exemption, because the author is still `dependabot[bot]`. The pushed code still needs both reviews and every check; only the description rules are skipped.
- **Self-tests.** `scripts/ci/test_pr_policy.py` extracts the policy script from the workflow and runs it for these cases (`CI helper scripts` runs them).

## Reusing these workflows in another repo

```yaml
jobs:
  api:
    uses: AnshSinghal-CyberUltron/AI_Mesh_Firewall/.github/workflows/reusable-python-ci.yml@main
    with:
      working-directory: .
      install-command: python -m pip install -e ".[dev]"
      lint-command: ruff check .
      typecheck-command: mypy --strict src
      test-command: pytest -q --junitxml=junit.xml
  security:
    uses: AnshSinghal-CyberUltron/AI_Mesh_Firewall/.github/workflows/reusable-secret-scan.yml@main
  sast:
    uses: AnshSinghal-CyberUltron/AI_Mesh_Firewall/.github/workflows/reusable-sast.yml@main
    permissions: { contents: read, security-events: write }
    with:
      bandit-targets: src
```

| Workflow | Inputs that matter |
|---|---|
| `reusable-python-ci.yml` | `working-directory`, `install-command`, `lint-command`, `typecheck-command`, `test-command`, `known-failures-file`, `min-tests`, `report-name` |
| `reusable-django-ci.yml` | `working-directory`, `install-command`, `test-labels`, `check-migrations`, `known-failures-file`, `min-tests`, `extra-env` |
| `reusable-node-ci.yml` | `working-directory`, `lint-command`, `test-command`, `build-command`, `node-version` |
| `reusable-secret-scan.yml` | `full-history`, image pins |
| `reusable-sast.yml` | `semgrep-configs`, `semgrep-severity`, `paths-exclude`, `bandit-targets`, `bandit-blocking` |
| `reusable-dependency-scan.yml` | `fail-on-severity`, `deny-licenses`, `dependency-review-required`, `osv-blocking`, `osv-exclude`, `npm-directories`, `npm-audit-blocking` |
| `reusable-infra-lint.yml` | `compose-files`, `compose-env`, `nginx-configs`, `paths-exclude` (hadolint and shellcheck), `trivy-skip-dirs`, `trivy-blocking`, `workflow-guard-script` |
| `reusable-codeql.yml` | `languages`, `queries`, `paths-ignore` |

**Permissions the caller must grant.** A called workflow can never have more than the calling job grants, so give the calling job at least:

| Workflow | Calling-job `permissions:` |
|---|---|
| `reusable-python-ci`, `reusable-django-ci`, `reusable-node-ci`, `reusable-secret-scan` | `contents: read` |
| `reusable-sast`, `reusable-codeql`, `reusable-infra-lint` | `contents: read`, `security-events: write` (SARIF upload) |
| `reusable-dependency-scan` | `contents: read`, `pull-requests: write` (review comment), `security-events: write` (OSV SARIF) |

Notes for callers:

- The `known-failures-file` ratchet runs `scripts/ci/known_failures.py`, so copy that file into the calling repo. The ratchet fails a run that did not complete; set `min-tests` near your suite's size so a partly collected suite fails too.
- Pin to a tag or commit (`@<sha>`), not `@main`, for reproducible builds.
- Every action in every workflow is pinned to a full commit SHA, with its version in a comment, and every workflow has a top-level `permissions: contents: read`; the [workflow guard](#workflow-guard-pinned-actions-and-least-privilege-tokens) fails CI otherwise, and Dependabot keeps the pins current. To run the guard in your repo, copy `scripts/ci/workflow_guard.py` and `scripts/ci/workflow_guard.requirements.txt` into one directory (default `scripts/ci/`, or point `workflow-guard-script` at the script). `reusable-infra-lint` fails when either file is missing; `workflow-guard-script: ''` skips the check. PyYAML in the requirements file is hash-pinned and bumped by hand: Dependabot's pip ecosystem does not cover `scripts/ci`.
- Scanner and service images are pinned by digest (`name:tag@sha256:…`). Dependabot's `docker` ecosystem does not see images set in workflow inputs, so bump them by hand: `docker pull name:newtag`, then copy the digest from `docker image inspect --format '{{index .RepoDigests 0}}'`.
- Command inputs (`install-command`, `test-command`, `compose-env`, …) are trusted shell, run with `eval`. Only the calling workflow sets them; never pass `github.event.*` or other PR-controlled text into them.

## Workflow guard: pinned actions and least-privilege tokens

A tag such as `@v4` can be moved to different code after review; a commit SHA cannot. A workflow without `permissions:` gives every job the repository's default token. `scripts/ci/workflow_guard.py` blocks both, in the `infra lint` job of `Security` on every PR and push. `CI helper scripts` runs its self-tests, which also check this repository's workflows.

| Rule | Fails when |
|---|---|
| `unpinned-uses` | a `uses:` is not in this repository (`./path`, or GitHub's `$/path` self-repository form), `docker://name@sha256:<64 hex>`, or `owner/repo[/path]@<40-hex commit SHA>`. A flow-style `{ uses: … }`, an alias or a block scalar as the value, and a value on the next line fail too. |
| `local-ref` | a `./path` or `$/path` does not resolve, inside the repository, to a workflow file or a directory with `action.yml`/`action.yaml`. The guard checks each local action it finds by the same rules, so a local composite action cannot hide an unpinned `uses:`. |
| `version-comment` | a SHA pin has no release comment (`# v4.2.1`). Reviewers read the comment, and Dependabot rewrites it with the SHA. |
| `permissions` | a workflow has no top-level `permissions:` (composite `action.yml` files are exempt). |
| `top-level-write` | the top-level block grants any `write` scope, or is `write-all` or `read-all`. Grant extra scopes on the job that needs them. |
| `unpinned-image` | a literal `jobs.<id>.container`, `container.image` or `services.<id>.image`, or a local action's `runs.image: docker://…`, has no `@sha256:` digest. An `${{ … }}` image is a notice only: pin its input default instead. |
| `unverifiable` | the file uses YAML that a line scanner cannot read the way a YAML parser does. Each of these fails: control characters, a carriage return not followed by a line feed, or the line breaks U+0085/U+2028/U+2029; a second document (`---` or `...` after content); explicit-key syntax (`? key` / `: value`); any quoted key (`"uses":`, `'uses':`, `"use\x73":`, `{"uses":x}`); a YAML tag (`!!str`, `!cancelled()` written without quotes); a quoted scalar or a flow collection that continues on the next line; and a top-level permission level other than a plain `read` or `none`. |
| `parser-mismatch` | PyYAML reads the file's `uses:` values or top-level `permissions` differently from the scanner. CI always runs this cross-check: the step installs the hash-pinned PyYAML in `scripts/ci/workflow_guard.requirements.txt` and passes `--require-yaml`. Run locally without PyYAML, the guard prints `parser cross-check off`. |

**How it reads YAML.**
- The scanner reads YAML line by line with the standard library. It skips block scalars (`run: |`), so script text is never mistaken for a key.
- It fails closed: where a line scanner and a YAML parser could disagree, it reports `unverifiable` rather than guess.
- In CI, PyYAML also parses every file, and any difference from the scanner's reading fails (`parser-mismatch`). A form the scanner misreads is therefore caught twice.
- Reviewer A's 26 review cases are self-tests (`scripts/ci/test_workflow_guard_review_cases.py`). `docs/plans/evidence/2026-11-11-ci-pin-actions-107/guard-s1-corpus.txt` runs 27 forms against PyYAML: every unpinned or over-privileged one fails.
- Local references resolve against the repository root, so run the guard from there or pass `--root`.

**Pinning an action.** Resolve the tag to the commit it points to; an annotated tag points to a tag object, so dereference it:

```bash
gh api repos/OWNER/REPO/git/ref/tags/vX.Y.Z --jq '.object | "\(.type) \(.sha)"'
# type "tag" (annotated): dereference to the commit
gh api repos/OWNER/REPO/git/tags/<sha from above> --jq '.object.sha'
# equivalent without the API: the `^{}` line is the commit of an annotated tag
git ls-remote --tags https://github.com/OWNER/REPO 'refs/tags/vX.Y.Z' 'refs/tags/vX.Y.Z^{}'
```

Then write `uses: OWNER/REPO@<commit sha> # vX.Y.Z`. Use the same pin as the other workflows for an action that is already used (`grep -rh 'OWNER/REPO@' .github/workflows | sort -u`), so Dependabot moves them together.

**Token scopes in use.** Every workflow's top level is `contents: read`. Only these jobs ask for more:

| Calling job in `security.yml` → job in the reusable workflow | Extra scope | Why |
|---|---|---|
| sast → `reusable-sast` semgrep, bandit; infra → `reusable-infra-lint` trivy-config; dependencies → `reusable-dependency-scan` osv; codeql → `reusable-codeql` analyze | `security-events: write` | upload SARIF to code scanning |
| codeql → `reusable-codeql` analyze | `actions: read` | CodeQL reads the workflow run it analyses |
| dependencies → `reusable-dependency-scan` dependency-review | `pull-requests: write` | `comment-summary-in-pr: on-failure` posts the summary on the PR |

Every other job, including all of `gateway-v2.yml`, `chat-pipeline-golden.yml`, `ci.yml` and `pr-policy.yml`, runs with `contents: read` only. `upload-artifact` authenticates with the runner's own runtime token, not `GITHUB_TOKEN`.

## One-time owner setup

1. **Branch protection.** The CI token cannot change protection, so the owner runs `./scripts/ci/setup_branch_protection.sh AnshSinghal-CyberUltron/AI_Mesh_Firewall` once.
   - It **replaces** any existing protection on `develop`, `staging` and `main`.
   - It skips a branch that does not exist, with a warning.
   - It turns on `enforce_admins`. Every agent acts as the owner's admin account, so without it the required checks would be advisory. `ENFORCE_ADMINS=false` restores the advisory behaviour; the script header shows how to lift it for one emergency merge.
   - Each required check is bound to the GitHub Actions app (id 15368), so a commit status of the same name posted by anyone else cannot satisfy it.
   - It leaves `required_conversation_resolution` off: agents cannot resolve review threads (GraphQL is blocked for them), so MERGE_POLICY rule 3 requires every code-scanning thread to be answered in a PR comment instead (fixed, dismissed with a reason, or tracked).
2. **Repository settings:**
   - Enable the *Dependency graph*. Until it is on, `dependency-review` reports "not supported" without blocking. Then set `dependency-review-required: true` in `security.yml`.
   - Enable *Dependabot alerts* and *Dependabot security updates*.
   - Enable *Code scanning*, which receives the SARIF uploads and CodeQL alerts.
   - **If the repository goes private:** code scanning (CodeQL and every SARIF upload) needs GitHub Advanced Security, which is paid for private repositories.
     - Without it, the SARIF upload steps of the other scanners fail softly (`continue-on-error`).
     - **CodeQL does not:** its `analyze` step uploads as part of the analysis and fails. CodeQL is not a required check, but MERGE_POLICY rule 2 would see a red check on every PR. Either buy Advanced Security or remove the `codeql` job from `security.yml`.
     - The scanners still run, still block where marked, and still publish their reports as run artifacts.
     - Only the Security-tab view is lost.
   - Enable *Private vulnerability reporting*.
3. **Optional paid or account tools.** GitGuardian, CodeRabbit, Snyk and SonarCloud are deliberately not wired. The owner chose free tools only. Each would add a job or app without changing the structure above.

## Running the checks locally

Every scanner runs from a pinned container image, so a local run matches CI exactly. For example:

```bash
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.30.1 git /repo --log-opts "--remerge-diff origin/develop..HEAD" --redact
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -oneline
python3 scripts/ci/workflow_guard.py .github/workflows   # add --require-yaml once PyYAML is installed
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep:1.185.0 semgrep scan --config p/python --baseline-commit origin/develop --error
python3 scripts/ci/known_failures.py --log django-test.log --baseline .github/ci/control-known-failures.txt
```
