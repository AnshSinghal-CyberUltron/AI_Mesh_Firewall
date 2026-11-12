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
| `gateway-v2` (`gateway-v2.yml`) | structural-gates | size, HTTP, mutability, capacity, tenant-scale AST gates; import-linter; ruff; mypy --strict; console inventory; the unsharded pytest collection that `gateway-v2 pytest` checks against | **blocks** |
| | gateway-v2 pytest shard 1/8 … 8/8, then **gateway-v2 pytest** | the full gateway_v2 pytest (5,162 tests), whole test files split across 8 runners, 300 s per test; `gateway-v2 pytest` fails unless every shard passed and the shards ran exactly the unsharded collection, once each ([how](#runners-sharding-and-timeouts)) | **blocks**; require `gateway-v2 pytest`, not the shards |
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
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep:1.185.0 semgrep scan --config p/python --baseline-commit origin/develop --error
python3 scripts/ci/known_failures.py --log django-test.log --baseline .github/ci/control-known-failures.txt
```

## Runners, sharding and timeouts

### The gateway_v2 suite runs in 8 shards

The full gateway_v2 pytest (5,162 tests) took 21.6 min as one step of `structural-gates`. It now runs as the matrix job `gateway-v2 pytest shard N/8` in `gateway-v2.yml`:

- **Split by whole test file.** Every shard collects the whole suite. `scripts/ci/amf_pytest_shards.py`, a stdlib pytest plugin, assigns whole files to shards and deselects the other shards' tests. Module-scoped fixtures and in-file test order therefore behave as in an unsharded run.
- **Balanced by measured durations.** The plugin uses longest-processing-time-first over `.github/ci/gateway-v2-test-durations.json` (per-file seconds from a CI run). A file missing from it weighs `tests × per_test_default`. Every weight is clamped to 0.001 s – 10⁶ s, and a value that is not a finite number ≥ 0 counts as missing, so zero, NaN or infinite weights cannot pile every file onto one shard. The weights only balance the shards: a stale or hostile file never drops or repeats a test.
- **Proved on every run.** `structural-gates` uploads the ids of the plain `pytest --collect-only -q`. `gateway-v2 pytest` downloads every shard's report and fails unless:
  - each shard index reported once;
  - all shards saw the same collection and the same assignment;
  - no test was selected twice;
  - the union of the shards equals the unsharded collection;
  - every shard's pytest exited 0;
  - every selected test ran to the end: each shard's report records every test's setup, call and teardown outcomes, and a selected test with no setup report, no call report after a passing setup, or no teardown report fails the check. A shard can exit 0 with tests left unrun: `pytest.exit(returncode=0)` in a test or fixture, or a `pytest_collection_modifyitems` hookwrapper in a conftest that drops items after the plugin selected them;
  - no shard reported a test it did not select.

  The comparison with the unsharded collection catches a drift that all shards share, such as another config file or a lost `--ignore`. `structural-gates` collects on `ubuntu-latest` while the shards follow `AMF_RUNNER_HEAVY`; if a larger runner with another image or architecture collects different ids, this check fails with the missing and extra ids (it never passes), and `structural-gates` should then move to the same runner.
- **Require `gateway-v2 pytest`, never the shards:** their names change with the shard count. It runs with `if: always()` because GitHub counts a *skipped* required check as passed; when a shard fails or the run is cancelled, it fails instead.
- **Plugin options are written `--option=value`, with `--config-file=pyproject.toml`.**
  - Pytest picks its rootdir and config file before it loads `-p` plugins. So `--shard-weights PATH` (with a space) made it treat PATH as a test path and read the repository root's `pyproject.toml`: 5,257 tests, no `--ignore` list, no timeout.
  - The plugin now refuses the space-separated form.

Change the shard count by editing the `shard:` list of the matrix; the plugin reads `strategy.job-index` and `strategy.job-total`. Each shard adds about 30 s of checkout, install and collection.

To refresh the weights, take the `gateway-v2-test-durations` artifact (or the log group of the same name) from a green `gateway-v2 pytest` job, and commit it as `.github/ci/gateway-v2-test-durations.json`.

Reproduce a shard, or the whole partition proof, locally from `gateway_v2/`:

```bash
PYTHONPATH=../scripts/ci python -m pytest --config-file=pyproject.toml -p amf_pytest_shards \
  --shard-index=3 --shard-count=8 --shard-weights=../.github/ci/gateway-v2-test-durations.json \
  --shard-report=/tmp/shard-3.json            # add --collect-only -q to list the ids only
python ../scripts/ci/amf_pytest_shards.py verify /tmp/reports --expect-collection collect.txt
```

### Runners

Jobs that mostly run tests read `runs-on: ${{ vars.AMF_RUNNER_HEAVY || 'ubuntu-latest' }}`:
- the gateway_v2 shards, `openai-conformance`, `parity-harness` and `image-digest`;
- control (Django), through the `runs-on` input of `reusable-django-ci`;
- CodeQL, through `reusable-codeql`.

`reusable-python-ci` and `reusable-node-ci` take the same input. With the repository variable `AMF_RUNNER_HEAVY` unset, everything stays on free standard runners. Set it to a larger runner's label to move those jobs without a workflow edit.

`image-digest` needs a runner with **at least 4 CPUs**: its LGW03-1 step runs the image with `--cpus=2` and `--cpus=4`, and Docker refuses a CPU limit above the host's count. GitHub's standard Linux runners have 4 CPUs for public repositories and 2 for private ones. So `AMF_RUNNER_HEAVY` must name a runner with 4 or more cores, and it must be set **before the repository goes private** (ENTERPRISE_SETUP.md, "When the repository goes private"); unset, `image-digest` stays on `ubuntu-latest`, which has 4 CPUs only while the repository is public. The step checks the CPU count first and says so.

Larger runners are billed per minute even for a public repository; on GitHub Team and Enterprise Cloud their cost can draw from the plan's included compute usage, the same allowance private-repository standard runners use (ENTERPRISE_SETUP.md step 3 quotes GitHub's billing page). A single pytest or Django process does not run faster on more cores. Creating the runners and the variable is an owner step.

### Timeouts

Every job has `timeout-minutes`, several times its measured duration, so a hang fails the job instead of running to GitHub's 360-minute default (issue #116):

| Job | Normal duration | Limit |
|---|---|---|
| structural-gates | about 1.2 min | 15 |
| gateway-v2 pytest shard | about 3–4 min | 20 |
| gateway-v2 pytest | under 1 min | 5 |
| openai-conformance, parity-harness, image-digest | 1–2 min | 15 |
| secret-headers | under 1 min | 5 |
| control (Django) | 2.2–3.1 min | 20 |
| golden-freeze | under 1 min | 10 |

Every reusable workflow's job already had one.

**Per test,** gateway_v2 uses pytest-timeout (`dev` extra), set to `timeout = 300` in `gateway_v2/pyproject.toml`. `required_plugins` there makes a missing plugin an error rather than a warning; the openai-conformance job, which runs pytest from `gateway_v2/` in its own venv, installs the same pin.
- SIGALRM fails the hung test, its log shows the stack and the test id, and the rest of the shard runs.
- 300 s is 3.6× the slowest test measured on CI (84 s).
- A slower machine sets `PYTEST_TIMEOUT=600`; it overrides the file, and `--timeout` overrides both.
