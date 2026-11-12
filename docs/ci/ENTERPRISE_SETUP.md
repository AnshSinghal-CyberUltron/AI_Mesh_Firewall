# GitHub Enterprise setup: the owner's click-list

The owner does these steps as an organisation and repository admin, **in this order**. Each step says where to click, what to choose and why. GitHub renames menu items from time to time; if a label differs, look for the nearest match. Facts marked *(read 12 Nov 2026)* were read through the REST API or on docs.github.com on that date.

## Agents and your token

Agents must not do these steps, and are meant not to be able to. But do not rely on that alone:

- **Agents act as you.** They reach GitHub through a proxy that presents **your account's token**. *(Read 12 Nov 2026, GET-only probes by Reviewer B on PR #138.)*
  - The proxy answers **403** to organisation endpoints (`orgs/*`), environments, Actions variables and permissions, collaborators, and GraphQL.
  - It answers **200** to repository rulesets (`repos/…/rulesets`, `repos/…/rulesets/19609105`, `repos/…/rulesets/rule-suites`) and to a run's pending deployments (`repos/…/actions/runs/{run_id}/pending_deployments`).
  - Whether it blocks the matching **writes** (PUT/DELETE on a ruleset, POST on `pending_deployments`) has not been tested, by design.
- **What that would allow if the writes pass:**
  - an agent session could switch a *repository* ruleset to `evaluate` or delete it. "No bypass actors" binds agents only for as long as they cannot edit the ruleset;
  - an agent session could approve a production deployment as you, if you are production's required reviewer. `can_admins_bypass: false` does not help: the agent *is* the reviewer.
- **So:**
  1. Ask the proxy's operator to confirm that it denies every non-GET method on `repos/*/rulesets*` and on `repos/*/actions/runs/*/pending_deployments`, and record the answer here.
  2. Create the ruleset at **organisation** level (step 10, `--org`). Organisation endpoints are 403 to agents, so they cannot change it.
  3. Make production's required reviewer a **human account whose token the agents do not hold** (step 10). `setup_environments.sh` refuses your own account there unless you override it.

## 0. Where things stand *(read 12 Nov 2026)*

| Item | State |
|---|---|
| Repository | `CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall`, **public** |
| `develop`, `staging` | **no protection at all**: no classic protection, no ruleset (`GET /rules/branches/develop` returns `[]`) |
| `main` | only ruleset 19609105 "Main Protected by Ansh", which the new ruleset replaces (step 11) |
| CodeQL | default setup **configured** (languages: actions, c-cpp, javascript, javascript-typescript, python, typescript; default query suite; weekly). `security.yml` also runs CodeQL ("advanced"), and GitHub rejects those uploads while default setup is on: `CodeQL / analyze (…)` has been red on every PR and push since the move |
| Secret scanning | on, with push protection, non-provider patterns and validity checks; AI detection off |
| Dependency graph | on (the SBOM lists 568 packages) |
| Dependabot security updates | **off** |
| Private vulnerability reporting | on |
| Actions concurrency | **queueing since the move**: on 12 Nov 04:00–06:40 UTC (90 runs, 420 jobs) at most 14 jobs ran at once, and jobs waited p50 3.7 min, p90 12.7 min, max 26 min for a runner. Before the move (11 Nov 06:00–11:00 UTC, 97 runs) the peak was 22, and p90 0.6 min. **While at least one job waited more than 15 s (12 Nov 04:00–08:00 UTC), this repository was running a median of only 7 jobs** (90th percentile 13; Reviewer B, from the Actions jobs API) |

## 1. Check the plan's Actions concurrency

**Where:** Organization → Settings → Billing and licensing (the plan and usage); Organization → Settings → Actions → Runners → GitHub-hosted runners (active jobs).

**What:** confirm that the organisation is on GitHub Enterprise for Actions, and whether other repositories in the organisation or enterprise hold runner slots.

**Why:**
- GitHub documents 500 concurrent jobs on standard runners for Enterprise (20 for Free) *(docs.github.com/en/actions/reference/limits, read 12 Nov 2026)*.
- Since the move, this repository has peaked at 14 running at once (22 before), and jobs wait a median 3.7 min and up to 26 min for a runner (before: median 0, p90 0.6 min). The numbers are in `docs/plans/evidence/2026-11-12-ci-gateway-v2-shards/actions-concurrency.txt` (PR #137).
- **The limit is not this repository's own demand.** Jobs queued while this repository ran a median of 7 jobs. So either other repositories under the same account hold the slots (the limit is per account), or GitHub's hosted capacity was short. This step tells which.
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
   | `amf-linux-4core` | Ubuntu latest, x64 | 4 cores, 16 GB | 20 | test suites and `image-digest` (`AMF_RUNNER_HEAVY`, step 4); **4 cores is the minimum** (`image-digest` runs `docker run --cpus=4`) |
   | `amf-linux-16core` | Ubuntu latest, x64 | 16 cores, 64 GB | 2 | scale and load tests, later (no workflow uses it yet; run them on `workflow_dispatch`, never on every PR) |

3. **Spending limit:** Enterprise or Organization → Billing → **Budgets and alerts** → new budget for Actions, with alerts at 75% and 90%, and **stop usage when the budget is reached**.

**What they cost** *(GitHub's billing page "How use of GitHub Actions is measured", docs.github.com/en/enterprise-cloud@latest/billing/concepts/product-billing/github-actions, and "Actions runner pricing", docs.github.com/en/enterprise-cloud@latest/billing/reference/actions-runner-pricing; both read 12 Nov 2026)*:
- "The included compute usage is expressed as a monetary amount, not a fixed number of minutes": **$300 USD a month on GitHub Enterprise Cloud**. "As a guide, $300 USD covers 50,000 minutes on a standard Linux runner at $0.006 USD per minute."
- Standard GitHub-hosted runners in public repositories are free and do not reduce it.
- **"Larger runners are billed for public repositories."** Under "The following usage can draw from the same included compute usage", the page lists "Larger runners, where available, when the billing account uses GitHub Team or GitHub Enterprise Cloud".
- So **setting `AMF_RUNNER_HEAVY` starts spending the plan's $300 today**, while the repository is public, at the larger-runner rate: Linux 4-core **$0.012 a minute** (16-core $0.042; the standard 2-core rate is $0.006).
- **Per push, measured:** a push to a PR runs 31 jobs, **68 billed minutes** (Reviewer B on #137's head `790e3e41`, from the Actions jobs API). `AMF_RUNNER_HEAVY` moves 15 of them: the 8 gateway_v2 shards, openai-conformance, parity-harness, image-digest, control and CodeQL ×3. That is **about 51 of the 68 billed minutes**: B measured 50 for the first 14, and image-digest takes under a minute.
- **Estimate** (multiplication, not a bill): 51 min × $0.012 ≈ **$0.61 a push** on 4-core runners. The $300 then covers about 490 pushes a month, if nothing else draws on it. On 11 Nov, 55 pushes ran the gateway-v2 workflow: about $34 a day, so the $300 would last about 9 days. Read the real figure in Billing after a day, and set the budget above before you set the variable.

**What they buy, and what they do not:**
- GitHub lists 1,000 concurrent jobs on larger runners for Enterprise, against 500 on standard runners *(docs.github.com/en/actions/reference/limits, read 12 Nov 2026)*. The page lists them as separate rows but does not say whether the two totals are separate; it says only that macOS concurrency is shared. So moving the test jobs there is **expected to** take them out of the step-1 queue; confirm it in the runner view after a day.
- The gateway_v2 shards and the control suite run one pytest/Django process each. On 4 cores they run about as fast as on today's free 4-vCPU standard runner, and more cores do not speed one shard up. More parallelism comes from more shards (PR #137), not bigger machines.
- Larger sizes pay off for work that uses every core: CodeQL, Docker builds, and future scale tests.

## 4. Repository variable `AMF_RUNNER_HEAVY`

**Where:** Repository → Settings → Secrets and variables → Actions → **Variables** tab → **New repository variable**.

**What:** name `AMF_RUNNER_HEAVY`, value `amf-linux-4core` (a label from step 3 with **at least 4 cores**).

**Why:**
- These jobs read `runs-on: ${{ vars.AMF_RUNNER_HEAVY || 'ubuntu-latest' }}` (PR #137):
  - the gateway_v2 pytest shards, openai-conformance and parity-harness;
  - `image-digest`, which needs **at least 4 CPUs**: its LGW03-1 step runs the image with `--cpus=2` and `--cpus=4`, and Docker refuses a CPU limit above the host's count;
  - the control Django suite;
  - CodeQL.
- Unset, they stay on free standard runners, which have 4 CPUs only while the repository is public (step 12).
- To go back, delete the variable; no workflow edit is needed. **Not once the repository is private:** `image-digest` then needs the variable (step 12).

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
- Organization → Settings → Copilot → Policies → **Copilot code review**: Enabled. It needs Copilot Business or Enterprise licences.
- Repository → Settings → Copilot → Code review: leave **Allow Copilot approvals to count toward merge requirements** off (the enterprise default disables it).

**What it costs** *(GitHub changelog 2026-04-27 "GitHub Copilot code review will start consuming GitHub Actions minutes on June 1, 2026", and the GitHub blog post "GitHub Copilot is moving to usage-based billing"; read 12 Nov 2026)*:
- Since 1 June 2026, Copilot usage, code review included, is billed in **GitHub AI Credits**, which replaced premium requests.
- Code review also consumes **Actions minutes** on private repositories; on public repositories they stay free. It runs on GitHub-hosted runners.

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

**Then, before requiring approvals:** confirm that the Apps' `APPROVE` reviews count toward the requirement, without loosening the real ruleset:
1. In the UI, add a temporary ruleset "AMF approvals test": target `develop`, enforcement **Evaluate**, one rule, *Require a pull request before merging* with **2** required approvals. Evaluate mode blocks nothing, and the main ruleset stays active.
2. Let both Apps approve a small test PR into `develop`, and merge it.
3. Repository → Settings → Rules → **Rule insights**: for that merge, the test ruleset's pull-request rule must show as passing. If it shows as failing, the Apps' approvals do not count: stop here.
4. Delete the test ruleset, re-run `setup_rulesets.sh` with `AMF_REQUIRED_APPROVALS=2` (and your other flags), and update `MERGE_POLICY.md` rule 1: reviews become `APPROVE` reviews from the Apps.

## 10. Run the two scripts

**When:** only after PRs #114, #137 and #138 have **all merged into `develop`**, from a clone of `develop` at or after that point. `required-checks.txt` requires `gateway-v2 pytest`, which exists only once #137 has merged; and step 2 must be decided first (or use `--without-codeql`).

Run them logged in with `gh auth login` as an admin. `--org` needs an organisation owner, or a token with the organisation *Administration* write permission. Both read their defaults from the repository files and change nothing in `--dry-run`, `--verify-sha` or `--check`.

```bash
# Rulesets: one ruleset for develop, staging and main. --org (recommended) makes it an
# organisation ruleset that targets this repository only; agents cannot reach organisation
# endpoints. Without --org it is a repository ruleset (agents can read it; see the top).
# Use the SAME flags in every command below.
scripts/ci/setup_rulesets.sh --org --dry-run                # read the JSON it would send
scripts/ci/setup_rulesets.sh --org --verify-sha <PR head>   # read-only: every required check succeeded there?
scripts/ci/setup_rulesets.sh --org --pr-head <PR head>      # apply, then it runs --check
scripts/ci/setup_rulesets.sh --org --check                  # read-only: in force as written?
#   <PR head>: the head SHA of a PR opened after #114, #137 and #138 merged, with every check green
#   --without-codeql   if step 2 kept default setup
#   --copilot-review   after step 8
#   --evaluate         "evaluate" mode first: GitHub reports what WOULD be blocked (Rule insights)
#                      and blocks nothing; run again without it to enforce
#   --no-strict        drop "require branches to be up to date" (not recommended: MERGE_POLICY.md)
#   AMF_REQUIRED_APPROVALS=2 after step 9

# Environments: staging and production, required reviewers, deployment branches.
# <second human login>: a human account whose token the agents do NOT hold (see the top).
scripts/ci/setup_environments.sh --dry-run --staging-reviewers user:<your login> --production-reviewers user:<second human login>
scripts/ci/setup_environments.sh --staging-reviewers user:<your login> --production-reviewers user:<second human login>
```

**What `setup_rulesets.sh` creates:** the ruleset "AMF protected branches: develop, staging, main" (active, no bypass actors, so admins and every agent are bound). Its rules:
- **deletion** and **non-fast-forward** blocked;
- **pull requests required**, with merge commits only, 0 approvals until step 9, and stale approvals dismissed;
- **required status checks:** every name in `.github/ci/required-checks.txt`, bound to the GitHub Actions app, with branches required to be up to date.

Safety checks before it writes anything (`--force` skips the first two):
- It aborts if a required check is already failing on `develop`'s head (for example CodeQL, before step 2), or if it cannot read the check runs there: the ruleset would block every merge.
- It aborts unless every required check **succeeded** on the `--pr-head` you give. A name that never reports (a renamed job, or running before #137 merged) would leave every PR waiting forever.
- It never writes "Main Protected by Ansh" (19609105), and aborts if two rulesets have its name.
- After writing, it runs `--check` (step 11) and says whether the result is OK.

**Residual risk with a repository ruleset** (without `--org`): until the proxy is confirmed to deny writes on `repos/*/rulesets*` (top of this page), an agent session acting as you could switch the ruleset to `evaluate` or delete it. Prefer `--org`. If you cannot create organisation rulesets, record the proxy operator's answer before step 11.

**Merge queue stays off:**
- With a queue, the REST merge endpoint refuses, and adding a PR to the queue is GraphQL-only. The agents' proxy blocks GraphQL, so no agent could merge.
- The workflows also do not run on `merge_group` yet.
- Turn it on with `--merge-queue` only after both are solved: you or an App outside the proxy enqueue PRs, and a PR adds `merge_group:` to every workflow behind a required check. (Organisation rulesets do not offer the merge-queue rule; it would need a repository ruleset.)
- Until then, "require branches to be up to date" gives the same guarantee one PR at a time. An agent updates a PR branch with the REST call `PUT /repos/{owner}/{repo}/pulls/{n}/update-branch`, and `MERGE_POLICY.md` says when that needs no new review. Repository → Settings → General → *Always suggest updating pull request branches* shows the button too.

**What `setup_environments.sh` creates:**
- Environments `staging` (deployable only from the `staging` branch) and `production` (only from `main`). **Owner decision (12 Nov 2026): production deploys from `main`**; `develop` → `staging` promotions deploy to `staging`.
- Each with your required reviewers, and admins **not** allowed to bypass them.
- **Production's required reviewer must be a human account whose token the agents do not hold.** Approving a waiting deployment is a REST call, and agents act as your account. The script refuses your own account (the account running it) as a production `user:` reviewer; `--allow-own-account-in-production` overrides that until a second human account exists, and prints the warning each time. A `team:` reviewer must not include your account either; the script cannot check team membership.
- No workflow deploys through them yet. A deploy job opts in with `environment: production` and then waits for that reviewer's approval.

## 11. Replace "Main Protected by Ansh" (id 19609105)

**Owner decision (12 Nov 2026):** the new ruleset replaces it. Its rules *(read 12 Nov 2026)*:

| Rule | Effect |
|---|---|
| `update` with **no bypass actors** | nobody may update `main`, and merging a PR updates it. **Every promotion merge into `main` is refused**, by any account |
| `pull_request`: 1 approval | no one but you can approve, and GitHub never counts an author's own approval, so a promotion PR opened from your account can never be approved |
| `pull_request`: code-owner review, extra approval for unattributed changes | there is no `CODEOWNERS` file, so the code-owner part requires nothing |
| `creation` | nobody may create `main` (harmless: it exists) |
| `deletion`, `non_fast_forward` | the new ruleset has them too |

Its target also lists `refs/heads/"main"`, with literal quotes, which matches no branch; `~DEFAULT_BRANCH` is what covers `main`.

**In this order. Never delete it first:** `main` would have no protection in between.

1. **Apply the new ruleset** (step 10): `--dry-run`, then `--verify-sha <PR head>`, then `--pr-head <PR head>`. Optionally apply with `--evaluate` first and read Rule insights, then apply again without it.
2. **Verify it.** Run `scripts/ci/setup_rulesets.sh --check` with the same flags. It prints `OK: 0 difference(s)` only when:
   - `GET` of the new ruleset shows `enforcement: active`, 0 bypass actors, the three branches and every required check bound to app 15368, exactly as the script writes it;
   - `GET repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/rules/branches/main` (and `develop`, `staging`) lists the new ruleset's `deletion`, `non_fast_forward`, `pull_request` and `required_status_checks` rules. On `main` it also lists 19609105's rules, as `info` lines.

   To see it by hand: `gh api repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall/rules/branches/main --jq '.[] | "\(.ruleset_id) \(.type)"'`. GitHub lists only **active** rules there, so an `evaluate` ruleset does not appear.
3. **Only then delete 19609105:** Repository → Settings → Rules → Rulesets → "Main Protected by Ansh" → `⋯` → **Delete ruleset**. The scripts never write it, and never will.
4. **Run `--check` again.** It must still say OK, and print "Ruleset 19609105 'Main Protected by Ansh': not readable (deleted, or no access)".

## 12. When the repository goes private

**Do these first. Each one blocks every PR if it is missing:**

| Before the switch | Why |
|---|---|
| **Set `AMF_RUNNER_HEAVY` to a runner with 4 or more cores** (steps 3–4), and see `image-digest` green on a PR on it | Standard Linux runners for **private** repositories have **2 CPUs and 8 GB**, against 4 CPUs and 16 GB for public ones *(docs.github.com/en/actions/reference/runners/github-hosted-runners, read 12 Nov 2026)*. `image-digest` is a required check, and its LGW03-1 step runs `docker run --cpus=4`, which Docker refuses on a 2-CPU host. It fails with "this runner has 2 CPUs; --cpus=4 needs 4 or more" (PR #137). **This is a blocker for going private.** |
| Buy **GitHub Code Security**, or remove the `codeql` job and re-run `setup_rulesets.sh --without-codeql` | CodeQL and every SARIF upload need it on a private repository; without it the required `CodeQL / analyze (…)` checks fail |
| Set `dependency-review-required: false` again if you do not buy Code Security | dependency review needs it on a private repository |

**What else changes:**

| Area | What changes |
|---|---|
| Actions minutes | Standard runners stop being free and draw on the plan's included compute usage, **$300 USD a month on Enterprise Cloud** ("As a guide, $300 USD covers 50,000 minutes on a standard Linux runner at $0.006 USD per minute"; step 3 cites the page). Larger runners draw on it already, and are billed while the repository is public too. "GitHub rounds the minutes and partial minutes each job uses up to the nearest whole minute" (the pricing page in step 3), so 8 shards cost more minutes than one long job. Measured on PR #137, the gateway-v2 workflow costs 43 billed minutes per push sharded, against 29 unsharded (`ci-timings.txt` in its evidence); all workflows together, 68 per push (step 3). Fewer shards trade speed for minutes |
| Test speed | Jobs left on standard runners run on **half the cores and memory** (2 CPUs and 8 GB instead of 4 and 16). Not measured here. The timing-sensitive GW28 tests flake under load, so the test jobs belong on `AMF_RUNNER_HEAVY` (which covers them) before the switch, not after |
| Copilot code review | consumes Actions minutes on private repositories (step 8) |
| Fork PRs | Forks become private and need your permission; the fork-approval setting matters less |
| CodeQL and every SARIF upload | need **GitHub Code Security** (paid). Without it the CodeQL job fails (its `analyze` step uploads), and the scanners' SARIF upload steps fail softly (`continue-on-error`). See the first table |
| Dependency review | needs GitHub Code Security too; without it set `dependency-review-required: false` again |
| Secret scanning and push protection | need **GitHub Secret Protection** (paid); without it they switch off. The gitleaks and TruffleHog jobs still run and block |
| Dependabot alerts and updates | stay free |
| Rulesets, required checks, required reviewers | available on Enterprise for private repositories |
| Environments | required reviewers and wait timers on private repositories need Enterprise (you have it); deployment branch rules keep working |
| Runner group | untick *Allow public repositories* |
| Artifact and cache storage | counts against the plan's storage. Shard reports are kept 7 days, durations 14 |

Check what is on with `gh api repos/CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall --jq .security_and_analysis` before and after the switch.
