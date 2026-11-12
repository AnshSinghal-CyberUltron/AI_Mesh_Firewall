# GitHub Enterprise setup: the owner's click-list

The owner does these steps as an organisation and repository admin, **in this order**. Agents cannot: their GitHub proxy blocks organisation endpoints, environments, Actions settings and GraphQL, and they act through the owner's account, so a setting they could change would also be one they could switch off.

Each step says where to click, what to choose and why. GitHub renames menu items from time to time; if a label differs, look for the nearest match. Facts marked *(read 12 Nov 2026)* were read through the REST API on that date.

## 0. Where things stand *(read 12 Nov 2026)*

| Item | State |
|---|---|
| Repository | `CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall`, **public** |
| `develop`, `staging` | **no protection at all**: no classic protection, no ruleset (`GET /rules/branches/develop` returns `[]`) |
| `main` | only ruleset 19609105 "Main Protected by Ansh"; see step 11 |
| CodeQL | default setup **configured** (languages: actions, c-cpp, javascript, javascript-typescript, python, typescript; default query suite; weekly). `security.yml` also runs CodeQL ("advanced"), and GitHub rejects those uploads while default setup is on: `CodeQL / analyze (…)` has been red on every PR and push since the move |
| Secret scanning | on, with push protection, non-provider patterns and validity checks; AI detection off |
| Dependency graph | on (the SBOM lists 568 packages) |
| Dependabot security updates | **off** |
| Private vulnerability reporting | on |
| Actions concurrency | **queueing since the move**: on 12 Nov 04:00–06:40 UTC (90 runs, 420 jobs) at most 14 jobs ran at once, and jobs waited p50 3.7 min, p90 12.7 min, max 26 min for a runner. Before the move (11 Nov 06:00–11:00 UTC, 97 runs) the peak was 22, and p90 0.6 min |

## 1. Check the plan's Actions concurrency

**Where:** Organization → Settings → Billing and licensing (the plan and usage); Organization → Settings → Actions → Runners → GitHub-hosted runners (active jobs).

**What:** confirm that the organisation is on GitHub Enterprise for Actions, and whether other repositories in the organisation hold runner slots.

**Why:**
- GitHub documents 500 concurrent jobs on standard runners for Enterprise (20 for Free).
- Since the move, this repository has peaked at 14 running at once (22 before), and jobs wait a median 3.7 min and up to 26 min for a runner (before: median 0, p90 0.6 min). The numbers are in `docs/plans/evidence/2026-11-12-ci-gateway-v2-shards/actions-concurrency.txt` (PR #137).
- Sharding the gateway_v2 suite (PR #137) cuts its critical path from about 22 minutes to under 5, but the 8 shards wait in the same queue. Every other step speeds up CI only once runners are available.

## 2. CodeQL: default setup or advanced

**Where:** Repository → Settings → Advanced Security (formerly "Code security") → Code scanning → CodeQL analysis → the `⋯` menu.

**Recommended: switch to advanced.** Choose **Disable CodeQL** (or **Switch to advanced**, then close the editor GitHub opens without committing its suggested workflow: `security.yml` already is the advanced setup).
- After that, `security.yml` uploads are accepted and `CodeQL / analyze (actions | javascript-typescript | python)` go green.
- Advanced setup keeps what default setup cannot: the `security-extended` query suite, `paths-ignore` for docs, evidence and vendored trees, and the same pinned action version as every other job.

**If you keep default setup instead:**
- It also analyses `c-cpp` and needs no workflow maintenance.
- But the `codeql` job in `security.yml` will stay red, so remove it in a PR.
- Run `setup_rulesets.sh` with `--without-codeql` (step 10).
- To block merges on CodeQL alerts, add a "Require code scanning results" rule (rule type `code_scanning`, tool CodeQL) to the ruleset in the UI: default setup's own check names are not in `required-checks.txt`.

## 3. Larger runners

**Where:** Organization → Settings → Actions → Runner groups → **New runner group**, then Actions → Runners → **New runner** → **New GitHub-hosted runner**.

**What:**
1. **Runner group** `amf-ci`:
   - Repository access: *Selected repositories* → `AI_Mesh_Firewall` only.
   - While the repository is public, tick **Allow public repositories**, or its jobs cannot use the group. That also lets PRs from forks run on billed runners; step 5 makes those wait for approval.
2. **Runners** in that group. The runner's name is the label workflows use:

   | Name (label) | Image | Size | Max concurrency | For |
   |---|---|---|---|---|
   | `amf-linux-4core` | Ubuntu latest, x64 | 4 cores, 16 GB | 20 | test suites (`AMF_RUNNER_HEAVY`, step 4) |
   | `amf-linux-16core` | Ubuntu latest, x64 | 16 cores, 64 GB | 2 | scale and load tests, later (no workflow uses it yet; run them on `workflow_dispatch`, never on every PR) |

3. **Spending limit:** Enterprise or Organization → Billing → **Budgets and alerts** → new budget for Actions, with alerts at 75% and 90%, and **stop usage when the budget is reached**.

**Why, and what it does not buy:**
- Larger runners are **always billed per minute, even for a public repository**. They do not use the plan's 50,000 included minutes.
- GitHub lists a separate concurrency total for larger runners: 1,000 jobs on Enterprise. So moving the test jobs there takes them out of the queue in step 1.
- The gateway_v2 shards and the control suite run one pytest/Django process each. On 4 cores they run about as fast as on today's free 4-vCPU standard runner, and more cores do not speed one shard up. More parallelism comes from more shards (PR #137), not bigger machines.
- Larger sizes pay off for work that uses every core: CodeQL, Docker builds, and future scale tests.

## 4. Repository variable `AMF_RUNNER_HEAVY`

**Where:** Repository → Settings → Secrets and variables → Actions → **Variables** tab → **New repository variable**.

**What:** name `AMF_RUNNER_HEAVY`, value `amf-linux-4core` (a label from step 3).

**Why:**
- The test-heavy jobs read `runs-on: ${{ vars.AMF_RUNNER_HEAVY || 'ubuntu-latest' }}`:
  - the gateway_v2 pytest shards, openai-conformance and parity-harness;
  - the control Django suite;
  - CodeQL.
- Unset, they stay on free standard runners.
- To go back, delete the variable; no workflow edit is needed.
- `image-digest` deliberately stays on `ubuntu-latest`: its LGW03-1 step runs the image with `--cpus 2` and `--cpus 4`.

**Never** set it to a self-hosted runner while the repository is public: a fork PR would run its code on your machine.

## 5. Organisation Actions policy

**Where:** Organization → Settings → Actions → General. An enterprise policy, if set, overrides these.

**What:**
1. **Policies:**
   - *Allow enterprise, and select non-enterprise, actions and reusable workflows* → *Allow actions created by GitHub*, plus the pattern `docker/setup-buildx-action@*`.
     - These are the only third-party actions the workflows use: `actions/*`, `github/codeql-action/*` and `docker/setup-buildx-action`.
   - Tick **Require actions to be pinned to a full-length commit SHA**.
2. **Workflow permissions:**
   - *Read repository contents and packages permissions* (read-only default `GITHUB_TOKEN`).
   - Untick *Allow GitHub Actions to create and approve pull requests*.
3. **Fork pull request workflows from outside collaborators:** *Require approval for all external contributors*.

**Why:**
- A tag such as `@v4` can be moved to other code after review; a SHA cannot. Every workflow already declares its own least-privilege `permissions:`, so a read-only default changes nothing for them and caps anything that forgets to.
- Approval stops a stranger's PR from consuming larger-runner minutes or reading `vars`.

**When:** only after PR #114 and PR #137 have merged into the branch the workflows run from. Before that, `gateway-v2.yml` and `chat-pipeline-golden.yml` on `develop` still use tag pins, and the SHA requirement would fail those runs.

## 6. Dependabot

**Where:** Repository → Settings → Advanced Security → Dependabot.

**What:** Dependabot alerts on; **Dependabot security updates on** (off today); grouped security updates on.

**Why:**
- `.github/dependabot.yml` already opens weekly grouped version-update PRs into `develop`. Security updates add PRs for the alerts: 178 open on the default branch (8 critical, 64 high), which need triage.
- The dependency graph is already on, so the `dependency-review` job can now block. Flip `dependency-review-required: true` in `security.yml` in a follow-up PR; it is `false` only because the graph used to be off.

## 7. Secret scanning

**Where:** Repository → Settings → Advanced Security → Secret Protection.

**What:**
- Keep secret scanning, push protection, non-provider patterns and validity checks on (they are on today).
- Optional: *Copilot secret scanning* (generic passwords).
- Leave *delegated bypass* off unless a second human can review bypass requests: every agent pushes as your account, so you would be approving your own bypasses.

**Why:** these are free while the repository is public. See step 12 for what changes when it goes private.

## 8. Copilot code review on every PR

**Where:**
- Organization → Settings → Copilot → Policies → **Copilot code review**: Enabled. It needs Copilot Business or Enterprise licences, and its reviews consume premium requests.
- Repository → Settings → Copilot → Code review: leave **Allow Copilot approvals to count toward merge requirements** off (the enterprise default disables it).

**Then:** run `setup_rulesets.sh --copilot-review` (step 10). This adds the `copilot_code_review` rule: Copilot reviews every PR into `develop`, `staging` and `main`, and every new push to it, but not drafts.

**Why:** it is an extra, automatic reviewer, as you asked. Copilot's approval must not satisfy the approval count: Reviewers A and B are the merge gate (`MERGE_POLICY.md`).

## 9. GitHub Apps for the agents' reviews

Today every agent review is posted from your account. GitHub never counts an author's own approval, so the agents post `COMMENT` reviews and a required approval count would block every merge. Two Apps give the two reviewer roles identities of their own, and their approvals count.

**Where:** Organization → Settings → Developer settings → GitHub Apps → **New GitHub App**. Do it twice: `amf-review-a` (correctness and tests) and `amf-review-b` (security, scale and operability, and B merges). One identity's approval counts once, so two required approvals need two Apps.

**What, for each App:**
- **Webhook:** off.
- **Repository permissions:**
  - Pull requests: **Read and write** (post reviews);
  - Contents: **Read-only**;
  - Checks: **Read-only**;
  - Metadata: Read-only (mandatory).
  - Nothing else, and no organisation or account permissions.
- **B only:** Contents **Read and write**, because merging needs it. Rulesets still bind B: it can merge only a PR whose required checks pass.
- **Where can this App be installed:** *Only on this account*.
- **Install:** Install App → *Only select repositories* → `AI_Mesh_Firewall`.
- **Private key:** Generate a private key.
  - The `.pem` file downloads once. Keep it in a secret manager or password manager outside every repository and every agent workspace.
  - **Never commit it, paste it into a chat, or put it in a file an agent can read.** An App mints 1-hour installation tokens from the key; whatever holds the key holds the App.

**Caveat:** the agents' sessions reach GitHub through a proxy that injects its own credentials (your account). Whether an agent session can present an App installation token instead is **untested**. Test it before you rely on it:
1. From an agent session, with a token minted outside the session, call `gh api /installation/repositories`.
2. Post a `COMMENT` review on a test PR, and confirm it shows as `amf-review-a[bot]`.

If the proxy replaces the token, the Apps need a runner that sits outside the proxy, or the proxy's configuration has to change.

**Then:** re-run `setup_rulesets.sh` with `AMF_REQUIRED_APPROVALS=2`, and update `MERGE_POLICY.md` rule 1: reviews become `APPROVE` reviews from the Apps.

## 10. Run the two scripts

Run them from a clone of the repository, logged in with `gh auth login` as an admin. Both read their defaults from the repository files and change nothing in `--dry-run`.

```bash
# Rulesets: one ruleset for develop, staging and main
scripts/ci/setup_rulesets.sh --dry-run                       # read the JSON it would send
scripts/ci/setup_rulesets.sh --verify-sha <head SHA of a recent green PR>   # read-only: every required check reported?
scripts/ci/setup_rulesets.sh                                 # apply; add --without-codeql if step 2 kept default setup
#   --copilot-review   after step 8
#   --evaluate         "evaluate" mode first: GitHub reports what WOULD be blocked (Rule insights) and blocks nothing
#   --no-strict        drop "require branches to be up to date"
#   AMF_REQUIRED_APPROVALS=2 after step 9

# Environments: staging and production, required reviewers, deployment branches
scripts/ci/setup_environments.sh --dry-run --staging-reviewers user:<your login> --production-reviewers user:<your login>
scripts/ci/setup_environments.sh --staging-reviewers user:<your login> --production-reviewers user:<your login>
```

**What `setup_rulesets.sh` creates:** the ruleset "AMF protected branches: develop, staging, main" (active, no bypass actors, so admins and every agent are bound). Its rules:
- **deletion** and **non-fast-forward** blocked;
- **pull requests required**, with merge commits only, 0 approvals until step 9, and stale approvals dismissed;
- **required status checks:** every name in `.github/ci/required-checks.txt`, bound to the GitHub Actions app, with branches required to be up to date.

Safety checks:
- It refuses to touch "Main Protected by Ansh".
- It aborts if a required check is already failing on `develop`'s head (for example CodeQL, before step 2), because the ruleset would then block every merge.

**Merge queue stays off:**
- With a queue, the REST merge endpoint refuses, and adding a PR to the queue is GraphQL-only. The agents' proxy blocks GraphQL, so no agent could merge.
- The workflows also do not run on `merge_group` yet.
- Turn it on with `--merge-queue` only after both are solved: you or an App outside the proxy enqueue PRs, and a PR adds `merge_group:` to every workflow behind a required check.
- Until then, "require branches to be up to date" gives the same guarantee one PR at a time. An agent updates a PR branch with the REST call `PUT /repos/{owner}/{repo}/pulls/{n}/update-branch`. Repository → Settings → General → *Always suggest updating pull request branches* shows the button too.

**What `setup_environments.sh` creates:**
- Environments `staging` (deployable only from the `staging` branch) and `production` (only from `main`).
- Each with your required reviewers, and admins **not** allowed to bypass them.
- No workflow deploys through them yet. A deploy job opts in with `environment: production` and then waits for your approval.
- If you meant production to deploy from the `staging` branch instead, pass `--production-branch staging`.

## 11. The existing ruleset "Main Protected by Ansh" (id 19609105)

The scripts never modify it, but it decides whether a promotion can ever reach `main`. Its rules *(read 12 Nov 2026)*:

| Rule | Effect |
|---|---|
| `update` with **no bypass actors** | nobody may update `main`, and merging a PR updates it. **Every promotion merge into `main` will be refused**, by any account |
| `creation` | nobody may create `main` (harmless: it exists) |
| `pull_request`: 1 approval, code-owner review, extra approval for unattributed changes | there is no `CODEOWNERS` file, and no one but you can approve. A promotion PR opened from your account can never be approved |
| `deletion`, `non_fast_forward` | same as the new ruleset |

Its target also lists `refs/heads/"main"`, with literal quotes, which matches no branch; `~DEFAULT_BRANCH` is what covers `main`.

**Decide one of:**
- delete it, since the new ruleset covers `main`;
- remove its `update` and `creation` rules and set approvals to 0 until step 9;
- add yourself as a bypass actor *for pull requests only*.

## 12. When the repository goes private

| Area | What changes |
|---|---|
| Actions minutes | Standard runners start counting against the plan's **50,000 included minutes a month** (Linux 1×). Every job's minutes round up to the next whole minute, so 8 shards cost more minutes than one long job. Measured on PR #137, the gateway-v2 workflow costs 43 billed minutes per push sharded, against 29 unsharded (`ci-timings.txt` in its evidence). Fewer shards trade speed for minutes. Larger runners stay billed per minute as before |
| Fork PRs | Forks become private and need your permission; the fork-approval setting matters less |
| CodeQL and every SARIF upload | need **GitHub Code Security** (paid). Without it the CodeQL job fails (its `analyze` step uploads), and the scanners' SARIF upload steps fail softly (`continue-on-error`). Buy it, or remove the `codeql` job and run `setup_rulesets.sh --without-codeql` |
| Dependency review | needs GitHub Code Security too; without it set `dependency-review-required: false` again |
| Secret scanning and push protection | need **GitHub Secret Protection** (paid); without it they switch off. The gitleaks and TruffleHog jobs still run and block |
| Dependabot alerts and updates | stay free |
| Rulesets, required checks, required reviewers | available on Enterprise for private repositories |
| Environments | required reviewers and wait timers on private repositories need Enterprise (you have it); deployment branch rules keep working |
| Runner group | untick *Allow public repositories* |
| Artifact and cache storage | counts against the plan's storage. Shard reports are kept 7 days, durations 14 |

Check what is on with `gh api repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall --jq .security_and_analysis` before and after the switch.
