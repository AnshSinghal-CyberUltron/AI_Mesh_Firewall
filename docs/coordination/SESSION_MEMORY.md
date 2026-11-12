# Session memory: AI Mesh Firewall build (10–12 Nov 2026)

This is everything the previous coordinator session knew that is not obvious from the code. Read it together with `HANDOFF-2026-11-12.md`, which has the live PR table and next actions. This file is the background and the rules.

It was written from the session's own record. It carries no secret values, by design: the transcript itself is not exported because it contains a token the owner pasted.

---

## 1. What the owner asked for (verbatim, still binding)

**Original brief:**

> analyze in detail the repository the suraj revamp branch and then merge all the changes to ansh branch and then start building whatever is remaining make sure you test everything end to end and build incrementally. one feature at a time and then do thorough testing and then validation exit criteria acceptance criteria scaling and then after all that then move to next feature ask clarifying questions

> make sure you work like a team of specialized ctos of a company and work like pros like they do follow all the procedure of software management like first dev branch then staging branch then main branch like these create prs review prs leave comments on prs ask other agents to leave comments on the pr do proper qa and everything end to end

> but make sure you do everything i dont have to merge the pr like the pr 100 you only ask another agent to review it and merge. create detailed test ci cd pipelines for auto checking tests like gitguard gitbot coderabbit and other tools frameworks and all security multiple reviewers and ci cd detailed pipelines for testing so that i can also reuse them in future

> just ignore this leaked credential thing i will rotate them later use them they are already dev env variables that have limited budget and limited access for development only so ignore them i will private repo after building is done

**11 Nov:**

> use highly specialized multi agent workflows and ship all the features in parallel but make sure not to skip any step for testing review qa proof of evidence exit criteria scaling live testing everything … use many special agents and workflows … maximum usage

**12 Nov:**

> I have taken github enterprise plan now and the new repo is CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall so use full benefits of github enterprise plan

**Owner preferences:**
- Terse and directive; expects autonomous execution.
- Rendered visual output is the quality gate (screenshots, not descriptions).
- Short, targeted corrections.
- **Always ask clarifying questions before building or executing; never assume.**

## 2. Owner decisions on record

| # | Decision |
|---|---|
| D1 | All v1 providers. F1a-3 ships the OpenAI-compatible family; F1b adds Azure and Anthropic native; F1c adds Bedrock. |
| D2 | Model API keys: "industry standard to scale to 100k RPS while keeping security". Means envelope-encrypted keys in signed state, with no control-plane call on the request path. |
| D3 | A model change made in the console reaches gateways live within seconds. |
| D4 | Env-var keys are refused in production. |
| D5 | Build locally and scale on this machine, hardware-independent; cloud later. |
| — | Agent merges need green checks plus 2 agent approvals. A check that fails identically on base may be waived with a written reason. |
| — | Production gateway first. |
| 11 Nov | **F1a-1 implementation waits until plan PR #103 is fully approved.** |
| 11 Nov | **F6:** audit first and render 2–3 visual directions. The owner picks before any build. |
| 11 Nov | **Every code PR also runs its full affected suites locally** and records them in the PR body. This became the bottleneck (§9). A move to GitHub runners was proposed and is unanswered. |
| 12 Nov | Repo moved to `CyberUltron-Consulting-Private-Limited/AI_Mesh_Firewall`; GitHub Enterprise plan. |

**HF token:** use it only through the `HF_TOKEN` env var. Never write it to a file, commit, log, PR or message.

## 3. Repository map (develop at `943a5f21`)

- **`gateway/`** is v1: the FastAPI gateway and **the live production data plane**. Prod compose builds `gateway/Dockerfile`, and nginx sends `/v1` to `gateway:8300`.
- **`gateway_v2/`** is v2, the new gateway:
  - `edge/` (serve, production_profile, wire, sse);
  - `dispatch/` (routing, stall guard, http providers, vector transports);
  - `detect/` (conditions, detectors);
  - `plan/`, `resolve/`;
  - `state_control/` (projector, writer, re-hydrator, signed stamps).

  In prod compose it is profile-gated off.
- **`control/`** is the Django control plane and console API (`ai_mesh_control/{core,policy,auth,mcp_connector,...}`).
- **`frontend/`** is the React console.
- **`shared/ai_mesh_shared`** is used by control and v1, not by gateway_v2.
- **`docs/plans/`** holds every design plan, and `docs/plans/evidence/<date>-<slug>/` holds the evidence behind each claim.
- **Branches:**
  - `develop` is the integration target for all PRs.
  - `staging` and `main` sit at `6cec3e28`. No promotion has run yet.
  - `revamp` is the original "ansh branch". PR #100 merged `suraj-revamp` into it.
  - `ansh`, `suraj-revamp`, `module2`, `module3`, `prodtest` and `main-copy` are legacy.

## 4. Feature roadmap

**F1: production gateway serves completions.**

| Slice | Contents | Status |
|---|---|---|
| F1a-1 | Pipeline safety: detector factory N5; customer-regex engine (translator to `regex` 2023.8.8, incremental streaming scan, tenant-fair scheduler, budgets); resolvers; audit sink; N14 Publisher rehydration; GW35; response store; hardening; boot log; reserved `_` slugs | Plan #103, revision 6 pending |
| F1a-2 | MODEL state | Plan #118, revision 2 pending |
| F1a-3 | Dispatch and credentials | Plan #121, reviews to rerun |
| F1b, F1c | Azure and Anthropic native; Bedrock | — |
| F1d | Last slice | — |

Exit L36-1: valid key gives a completion, streaming and non-streaming; invalid key gives a 401; a blocked prompt makes zero upstream calls; trace, metrics and console agree via a per-run nonce.

**F2: real local guard model.** PG2-22M fp32 ONNX export with parity, manifest-verified, off-loop semantic stage, N12/N13. Exit L36-2: measured recall/FPR and per-window latency. Branch `plan/f2-guard-model` (draft).

**F3:** all features live on the production boot (GW36 S7). Depends on F1.

**F4: fix open defects.** The register is on #115: `docs/plans/evidence/2026-11-11-f4-defects/REGISTER.md`, items R1–R17.

**F5:** local scale testing. After F1.

**F6:** frontend redesign plus public landing page. Branch `plan/f6-frontend-directions` holds the audit and three directions: Checkpoint, Night Shift, Redline.

## 5. PR history

| PR | What | Result |
|---|---|---|
| #100 | `suraj-revamp` → `revamp` | merged |
| #101 | release blockers from the #100 review | merged |
| #102 | reusable CI/CD and security pipelines, PR policy, merge policy | merged `f3f64a58` |
| #104, #105 | GW34 scan-calibration hardening and follow-ups | merged |
| #108 | protection and scanner follow-ups | merged `25c33c86` |
| #109, #110 | GW34 remedy wording; load-robust GW28 tests | merged together `50cb47d6` |
| #111 | gitleaks count ignores `.gitattributes` | merged `943a5f21` |
| #103 | F1 plan | open, rev 5, REQUEST CHANGES ×2 |
| #112–#118, #121, #122 | | open; see HANDOFF §2 |
| #106 | control security tests must be fixed, not baselined | fixed by #113 (open) |
| #107 | SHA-pin actions | fixed by #114 (approved, not merged) |
| #116 | CI timeouts | issue |
| #119 | GW28 stall-test gaps | issue |
| #120 | cross-tenant superuser, **High** | fixed by #122 (open) |

## 6. CI/CD that exists (reusable; see `docs/ci/README.md`)

**Workflows:**
- `ci.yml` and `security.yml` call the reusables:
  - `reusable-python-ci`;
  - `reusable-django-ci`;
  - `reusable-node-ci`;
  - `reusable-secret-scan` (gitleaks `--remerge-diff`, with the commit-count guard and the `GIT_ATTR_SOURCE` empty-tree count; TruffleHog `--results=verified`, raw output redacted);
  - `reusable-sast` (Semgrep, Bandit);
  - `reusable-codeql`;
  - `reusable-dependency-scan` (OSV, npm audit, dependency-review);
  - `reusable-infra-lint` (actionlint with shellcheck, Trivy config, the workflow guard from #114).
- `pr-policy.yml` checks:
  - a Conventional Commits title;
  - a description of minimum length;
  - **a heading containing Test, Evidence, Verification or Validation**;
  - promotion-path rules.
- `gateway-v2.yml` holds `structural-gates`, `openai-conformance`, `parity-harness` and `image-digest`.
- `chat-pipeline-golden.yml` holds `golden-freeze`, the v1 golden suite:
  - It fails 153 tests on base, so it is waived when identical (see R16).
  - From #114 it also runs on PRs that touch its own workflow file.

**Policies:**
- `docs/ci/MERGE_POLICY.md`:
  - rules 1–5;
  - waivers;
  - **rule 6, added in #114: B signs off on `.github/`, `scripts/ci/`, `docs/ci/` and `.gitleaks*`, and the PR body names every gate it relaxes.**
- `docs/ci/SECURITY.md` covers acknowledged dev credentials and scanner limits.

**Ratchets and scripts:**
- `.github/ci/control-known-failures.txt` is the control test ratchet, enforced by `scripts/ci/known_failures.py`.
- `scripts/ci/setup_branch_protection.sh` sets branch protection; it is the owner's to run.

**Pinning and conventions:**
- All actions are pinned to a full SHA with a version comment.
- All images are pinned by digest. This is fully true once #114 merges.
- Commit and PR attribution lines are required: see `AUTHOR_GUIDE.md`.

## 7. How the team worked (repeat this)

- **The coordinator**, the main session:
  - assigns tracks, one agent per worktree and branch;
  - spawns **two fresh reviewers per PR**:
    - Reviewer A covers completeness and correctness;
    - Reviewer B covers security, scale and operability, and **merges**;
  - collects owner questions in batches;
  - never merges and never approves its own PR.
- **Plan PRs come first** for every feature slice: design, then two plan reviewers, then a revision, then approval. Only then is code written.
- **Reviews** are posted as `event=COMMENT` because a single owner account cannot approve its own PRs.
  - Line 1 states the verdict, e.g. `**Reviewer B (security, scale & operability): APPROVE**`.
  - Merge with `merge_method=merge` and `sha=<head>`, then post a merge comment.
- **Every finding is answered:** fixed, tracked in an issue, or argued with evidence. Every code-scanning thread is answered.
- **Reviewers attack**, they don't skim:
  - mutation testing (revert the fix, the test must fail);
  - live attack harnesses against the real URLs and DB;
  - reproducing the author's numbers.
- **Author agents** hand back a fixed set of fields: PR number, full head SHA, what was run, focus areas for reviewers, and anything unresolved.
- **Briefs** live in `AUTHOR_GUIDE.md` and `REVIEWER_GUIDE.md` in this folder. They still carry old-host paths; recreate those.

## 8. Key technical findings (for continuity)

**Live at develop:**
- #117: a nested customer regex such as `(`×499, or deep plan JSON, raises `RecursionError` out of the projector round. The heartbeat ages and the whole fleet fails closed. This is a fleet-wide DoS.
- The same class sits in the gateway state applier (`state_task.py`, `delta.py`, `_envelope`).

**v1 prod (R16):** 21 obfuscated, encoded or split PII inputs reach the model.

**Control (#120):** superusers cross org boundaries on incident resolve and escalate, and in notifications.

**Built-in rules (#115, R1):** they were published with `surfaces: ["chat"]` only, so MCP, RAG, vector and embeddings were unprotected.

**Plan #103 (customer regex):**
- `regex` 2023.8.8 is pinned because it is Unicode 15.0, matching Python 3.12.
- `regex` bugs found and worked around:
  - lone negated characters in an alternation are merged, so `[^x]` is emitted doubled;
  - the first-character prefilter folds case wrongly, so a `(?=[\s\S]|\Z)` guard is added;
  - repeat minimums are unrolled, leading to a compile bomb, so there is a compile-units cap.
- Its timeout counts process CPU, so a thread-CPU patch was built (`clock/CLOCK.md`).
- Nesting depth is capped at 16.
- The evidence is in `docs/plans/evidence/2026-11-12-f1-plan-rev6-wip/`.

**F1a-3:**
- httpx costs about 3.2 ms of CPU per streamed request; aiohttp about 0.36 ms. Owner question Q5.
- Develop defects found: C3–C13 and R17 (HANDOFF §3).

**F1a-2:** the projector's source read, repair at 100k orgs, the MODEL heartbeat, the byte-bounded LRU and the breaker all need redesign (reviews in `reviews/`).

**Policy for the future:**
- Treat "one tenant's data stops the fleet" as a first-class review item on every state-plane PR.
- Treat timing-sensitive tests (GW28) as flaky under load. Make them robust; never weaken them.
  - Use `RELAXED` bounds for tests that are not about stalls.
  - Benchmarks measure thread CPU plus the wall-clock median.

## 9. Operational lessons

- **GitHub access:**
  - Use REST `gh api`; GraphQL was blocked.
  - Third-party repos via `gh api` return 403; use `git ls-remote` instead. Code-scanning alert endpoints also return 403.
  - The PR body footer gets rewritten in transit to `_Generated by [Claude Code](…)_`. This is harmless.
- **Session and repo layout:**
  - **Two repositories with the same name cannot be attached to one session.** That is why this handoff exists.
  - **The old path is a redirect to the org repo. Never delete it: that would delete the repo.**
- **Test hygiene:**
  - `tests/openai_conformance/sdk_matrix/conftest.py` rewrites a committed evidence file on every run. Discard that diff.
- **Shared host:**
  - One 2-CPU host shared by about 20 agents is too small. Full gateway_v2 takes 14 min idle and 25–50 min under load, and the heavy-job lock queued about 14 jobs.
  - Use GitHub larger runners and the Enterprise minutes for full suites, with targeted local runs (pending owner confirmation).
  - Serialise long local jobs with `flock` on a heavy lock, and control tests with a DB lock. Control tests share one Postgres.
- **Shell:** don't background with `&` inside a background tool call; the process gets reaped.
- **Subagents:**
  - Subagents may be blocked from writing `.md`; use `.txt` for gh body files.
  - The subagent permission classifier denies `add_repo` and opening issues; the coordinator does those.

## 10. Open owner questions (ask as one batch, with recommended defaults)
See HANDOFF §5. Topics: F1a-2 Q1–Q7 and four more; F1a-3 Q1–Q12; whether removing the "task 3.1" Tier-1 injection floor was intended; local full suites versus GitHub runners; the GitHub Enterprise setup in HANDOFF §6.

## 11. Owner actions outstanding
- Turn on "require actions pinned to full-length SHA" and set the default token to read-only.
- Turn on the dependency graph and Dependabot security updates. There are 178 alerts on the default branch: 8 critical, 64 high.
- Rulesets and merge queue on `develop`, `staging` and `main`; environments `staging` and `production` with required reviewers.
- Decide on the GHAS add-ons (Secret Protection, Code Security) now that the repo is private under an org.
- Decide on Copilot code review as an extra automatic reviewer.
