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
| | | Trivy config (HIGH, CRITICAL) | report → code scanning |
| | CodeQL | Python, JavaScript/TypeScript, GitHub Actions; `security-extended` | alerts → code scanning |
| `PR policy` (`pr-policy.yml`) | policy | Conventional Commit title, description with a testing section, size report, promotion path, release notes into `main` | **blocks** |
| `chat-pipeline-golden` | golden-freeze | v1 gateway golden tests | **red since 6 Aug 2026 on every branch**; waived per `MERGE_POLICY.md` until fixed |
| Dependabot (`dependabot.yml`) | | weekly grouped update PRs into `develop` for Actions, pip, npm and Docker | |

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
| `reusable-infra-lint.yml` | `compose-files`, `compose-env`, `nginx-configs`, `paths-exclude` (hadolint and shellcheck), `trivy-skip-dirs`, `trivy-blocking` |
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
- Every action in the reusable workflows, `ci.yml`, `security.yml` and `pr-policy.yml` is pinned to a full commit SHA, with its version in a comment; Dependabot keeps them current. The older `gateway-v2.yml` and `chat-pipeline-golden.yml` still use tag pins and have no `permissions:` block; moving them is tracked in issue #107.
- Scanner and service images are pinned by digest (`name:tag@sha256:…`). Dependabot's `docker` ecosystem does not see images set in workflow inputs, so bump them by hand: `docker pull name:newtag`, then copy the digest from `docker image inspect --format '{{index .RepoDigests 0}}'`.
- Command inputs (`install-command`, `test-command`, `compose-env`, …) are trusted shell, run with `eval`. Only the calling workflow sets them; never pass `github.event.*` or other PR-controlled text into them.

## One-time owner setup

**Today *(read 12 Nov 2026)*:** `develop` and `staging` have **no protection at all**: no classic branch protection, no ruleset. `main` has only the ruleset "Main Protected by Ansh" (id 19609105), which the ruleset below replaces ([ENTERPRISE_SETUP.md](ENTERPRISE_SETUP.md) step 11). Until the owner applies it, `MERGE_POLICY.md` and Reviewer B enforce the required checks and the PR-only flow, not GitHub.

1. **Ruleset.** The CI token cannot change protection, so the owner runs `scripts/ci/setup_rulesets.sh` (`--dry-run` first), from `develop` once #114, #137 and #138 have merged. It creates or updates ONE ruleset, "AMF protected branches: develop, staging, main"; with `--org` (recommended, because agents act through the owner's token and cannot reach organisation endpoints) it is an organisation ruleset that targets this repository only:
   - active, with **no bypass actors**: admins, and every agent (they act as the owner's admin account), are bound, as `enforce_admins` did for classic protection;
   - deletion and force-push blocked;
   - changes only through PRs;
   - merge commits only;
   - required approvals 0 until agent reviews come from GitHub Apps (`AMF_REQUIRED_APPROVALS`, ENTERPRISE_SETUP.md step 9);
   - every check in `.github/ci/required-checks.txt` required, bound to the GitHub Actions app (id 15368), so a commit status of the same name posted by anyone else cannot satisfy it;
   - branches must be up to date with the base before merging;
   - no conversation-resolution rule: agents cannot resolve review threads (GraphQL is blocked for them), so MERGE_POLICY rule 3 requires every code-scanning thread to be answered in a PR comment instead (fixed, dismissed with a reason, or tracked);
   - behind flags: Copilot code review (`--copilot-review`) and the merge queue (`--merge-queue`, off because enqueueing is GraphQL-only, so agents could not merge).

   Before writing, it aborts if a required check is already failing on `develop`'s head or cannot be read there, and unless every required check succeeded on the PR head given with `--pr-head`. After writing it runs `--check`, which compares the live ruleset with what it writes and requires its rules in `GET rules/branches/{develop,staging,main}`. Only when `--check` is OK does the owner delete "Main Protected by Ansh"; the script never writes it. It replaces `setup_branch_protection.sh` (classic protection, never applied; removed).

   `required-checks.txt` lists only checks that run on every PR into `develop`, `staging` and `main`. `scripts/ci/test_setup_scripts.py` fails when a listed name no longer matches such a job, so a renamed job cannot silently leave every PR waiting for a check that never reports. Re-run the script after a change to the file merges.
2. **Environments.** `scripts/ci/setup_environments.sh` creates `staging` (deployable from the `staging` branch) and `production` (from `main`, the owner's decision of 12 Nov 2026), with required reviewers. Production's reviewer must be a human account whose token the agents do not hold; the script refuses the account running it there unless overridden.
3. **Everything else** (Actions concurrency, CodeQL default vs advanced setup, larger runners, the organisation Actions policy, Dependabot, secret scanning, Copilot code review, GitHub Apps for the agents' reviews, and what changes when the repository goes private) is the owner's click-list in [ENTERPRISE_SETUP.md](ENTERPRISE_SETUP.md), in order.
4. **Optional paid or account tools.** GitGuardian, CodeRabbit, Snyk and SonarCloud are deliberately not wired. The owner chose free tools only. Each would add a job or app without changing the structure above.

## Running the checks locally

Every scanner runs from a pinned container image, so a local run matches CI exactly. For example:

```bash
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.30.1 git /repo --log-opts "--remerge-diff origin/develop..HEAD" --redact
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -oneline
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep:1.185.0 semgrep scan --config p/python --baseline-commit origin/develop --error
python3 scripts/ci/known_failures.py --log django-test.log --baseline .github/ci/control-known-failures.txt
```
