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
| `CI` (`ci.yml`) | control | Django tests, Postgres 16 + Redis 7.4 service containers | **blocks** on any failure not listed in `.github/ci/control-known-failures.txt` |
| | frontend | `npm ci`, `node --test` unit tests, `vite build` | **blocks** |
| | CI helper scripts | self-tests for `scripts/ci/known_failures.py` | **blocks** |
| `gateway-v2` (`gateway-v2.yml`) | structural-gates | size, HTTP, mutability, capacity, tenant-scale AST gates; import-linter; ruff; mypy --strict; console inventory; ~5,000 pytest | **blocks** |
| | secret-headers, image-digest, openai-conformance, parity-harness | repo-specific gates | **block** |
| `Security` (`security.yml`) | secrets | gitleaks + TruffleHog (verified only), PR commits only | **blocks** |
| | SAST | Semgrep OSS (diff-aware: new ERROR findings only) | **blocks** |
| | | Bandit (high severity, high confidence) | report → code scanning |
| | dependencies | dependency-review: new deps with high+ CVEs or AGPL/SSPL licences | **blocks** |
| | | OSV-Scanner on every lockfile, npm audit | report → code scanning |
| | infra lint | actionlint, hadolint (error), shellcheck (error), `docker compose config`, `nginx -t` | **blocks** |
| | | Trivy config (HIGH, CRITICAL) | report → code scanning |
| | CodeQL | Python, JavaScript/TypeScript, GitHub Actions; `security-extended` | alerts → code scanning |
| `PR policy` (`pr-policy.yml`) | policy | Conventional Commit title, description with a testing section, size report, promotion path, release notes into `main` | **blocks** |
| `chat-pipeline-golden` | golden-freeze | v1 gateway golden tests | **red since 6 Aug 2026 on every branch**; waived per `MERGE_POLICY.md` until fixed |
| Dependabot (`dependabot.yml`) | | weekly grouped update PRs into `develop` for Actions, pip, npm and Docker | |

The weekly `schedule` run of `Security` scans the **full git history** for secrets and every lockfile, so it catches anything the diff-aware PR checks cannot see.

### Why some scanners are report-only

The repo already carries findings: 319 OSV advisories, root containers flagged by Trivy, and legacy Bandit hits. A scanner that blocks on legacy findings keeps every PR red until people stop reading it. So:

- **Diff-aware scanners block.** Secrets on the PR's commits, Semgrep `--baseline-commit`, and dependency-review only see what the PR adds.
- **Whole-repo scanners report** to the Security tab.

Flip `osv-blocking`, `bandit-blocking`, `npm-audit-blocking` or `trivy-blocking` to `true` in `security.yml` once a scanner is at zero.

### Baselines (they may only shrink)

| File | What it holds | Rule |
|---|---|---|
| `.github/ci/control-known-failures.txt` | 23 control tests failing when the gate was introduced | delete a line when its test passes (the ratchet prints a notice); never add one |
| `.gitleaksignore` | fingerprints of findings in **old commits** (no secret values) | never add a fingerprint for a new commit; remove the secret instead |
| `.gitleaks.toml` | allowlist for synthetic credentials in tests, specs and evidence | never widen it to hide a real finding |

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
| `reusable-python-ci.yml` | `working-directory`, `install-command`, `lint-command`, `typecheck-command`, `test-command`, `known-failures-file`, `report-name` |
| `reusable-django-ci.yml` | `working-directory`, `install-command`, `test-labels`, `check-migrations`, `known-failures-file`, `extra-env` |
| `reusable-node-ci.yml` | `working-directory`, `lint-command`, `test-command`, `build-command`, `node-version` |
| `reusable-secret-scan.yml` | `full-history`, image pins |
| `reusable-sast.yml` | `semgrep-configs`, `semgrep-severity`, `paths-exclude`, `bandit-targets`, `bandit-blocking` |
| `reusable-dependency-scan.yml` | `fail-on-severity`, `deny-licenses`, `osv-blocking`, `osv-exclude`, `npm-directories`, `npm-audit-blocking` |
| `reusable-infra-lint.yml` | `compose-files`, `compose-env`, `nginx-configs`, `paths-exclude`, `trivy-blocking` |
| `reusable-codeql.yml` | `languages`, `queries`, `paths-ignore` |

Notes for callers:

- The `known-failures-file` ratchet runs `scripts/ci/known_failures.py`, so copy that file into the calling repo.
- Pin to a tag or commit (`@<sha>`), not `@main`, for reproducible builds.
- Every third-party action here is pinned to a full commit SHA, with its version in a comment. Dependabot keeps them current.

## One-time owner setup

1. **Branch protection.** The CI token cannot change protection, so the owner runs `./scripts/ci/setup_branch_protection.sh AnshSinghal-CyberUltron/AI_Mesh_Firewall` once.
2. **Repository settings:**
   - Enable *Dependabot alerts* and *Dependabot security updates*.
   - Enable *Code scanning*, which receives the SARIF uploads and CodeQL alerts.
   - Enable *Private vulnerability reporting*.
3. **Optional paid or account tools.** GitGuardian, CodeRabbit, Snyk and SonarCloud are deliberately not wired. The owner chose free tools only. Each would add a job or app without changing the structure above.

## Running the checks locally

Every scanner runs from a pinned container image, so a local run matches CI exactly. For example:

```bash
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.30.1 git /repo --log-opts "origin/develop..HEAD" --redact
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12 -oneline
docker run --rm -v "$PWD:/src" -w /src semgrep/semgrep:1.185.0 semgrep scan --config p/python --baseline-commit origin/develop --error
python3 scripts/ci/known_failures.py --log django-test.log --baseline .github/ci/control-known-failures.txt
```
