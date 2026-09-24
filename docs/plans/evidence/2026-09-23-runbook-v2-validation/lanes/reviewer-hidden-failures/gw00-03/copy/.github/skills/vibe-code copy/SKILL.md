---
name: multi-agent-codebase-analysis-and-fix
description: >
  Use this skill whenever a user wants to deeply analyze an entire codebase (frontend, backend, gateway, or any multi-layer architecture) to find all bugs, design problems, performance issues, or security flaws, and then fix them in a structured, production-grade way.
  Trigger this skill when the user says things like: "find all problems in my repo", "analyze my codebase end to end", "run parallel agents on my code", "deep codebase audit", "fix everything in the repo", "explore the entire code and fix issues", "multi-agent codebase review", or any phrasing that implies a comprehensive, automated, multi-pass review and remediation of a software project.
  This skill orchestrates parallel sub-agents at every layer — exploration agents, dynamic problem-analysis agents (minimum 10, scaled to complexity), solution-research agents, log-watching agents, and verification agents — all running concurrently wherever possible. Drives a deterministic plan-execute-verify lifecycle using MCP tools (GitHub, Context7, Playwright, Linear), full system access, open-source references, and all available superpowers. Sequential execution is the exception, not the rule.
---

# Multi-Agent Codebase Analysis & Fix Skill

A production-grade skill for exhaustive, parallelized codebase exploration, problem discovery, solution research, and implementation — executed with strict lifecycle discipline.

---

## Skill Execution Lifecycle (MANDATORY — never skip a phase)

Every execution MUST follow this sequence. Each phase gates the next.

```
Phase 1  →  @superpowers /brainstorming     (design solution space BEFORE touching code)
Phase 2  →  @superpowers /debug             (systematic root-cause with real evidence)
Phase 3  →  @superpowers /plan              (deterministic plan from diagnosed causes)
Phase 4  →  @superpowers /execute           (step-by-step execution with checkpoints)
Phase 5  →  @superpowers /review            (architecture + code-quality review)
Phase 6  →  @superpowers /verify            (live production workflow confirmation)
```

Do NOT collapse phases. Do NOT skip to /execute without completing /brainstorming, /debug, and /plan first.

> ⛔ **GIT PUSH / MERGE / COMMIT TO REMOTE IS STRICTLY FORBIDDEN.**
> All code changes stay LOCAL only. Do NOT `git push`, do NOT open PRs, do NOT merge branches to any remote. Git is used only for local branching and worktrees to safely isolate changes during development. No remote operations of any kind.

---

## Phase 1 — /brainstorming: Solution Space Design

Before writing a single line of code or changing any file:

1. **Identify the repository scope**
   - What layers exist? (frontend / backend / gateway / infra / shared libs)
   - What languages and frameworks are present?
   - What is the CI/CD and deployment mechanism?
   - Are there existing test suites, linting configs, or type-checkers?

2. **Map available MCP tools** to tasks:
   - `GitHub MCP` → repo exploration, PR/issue creation, diff viewing, file reading
   - `Context7` → documentation lookup, library-specific best practices
   - `Playwright MCP` → live UI/workflow validation in Phase 6
   - `Linear MCP` → issue tracking for found problems

3. **Design the parallel agent topology — MAXIMIZE PARALLELISM AT EVERY LAYER:**
   ```
   Layer 1: 10+ Exploration Agents        → produce Memory Files (one per codebase region)
   Layer 1.5: Live Service Boot           → start all services + parallel log-watcher agents
   Layer 2: N Analysis Agents per file    → minimum 10 per file, scale up if scope is large
                                            (e.g. 15-20 agents per file for complex layers)
   Layer 3: 10+ Solution Research Agents  → all run in parallel, never serial
   Layer 4: 1 Synthesis Agent             → 1 Master Implementation Plan
   Layer 5: Execution — parallel worktrees where possible
   Layer 6: Verify — parallel curl agents + parallel log-watcher agents + Playwright
   ```
   > **PARALLELISM RULE (STRICT):** At every layer, dispatch ALL agents concurrently. Never run agent N+1 after agent N completes. If two tasks can logically run at the same time, they MUST run at the same time. Sequential execution is only permitted when there is an explicit data dependency.

4. **Apply skill-brainstorming** discipline: generate at least 3 architectural hypotheses about where the biggest risks are likely to live before dispatching agents.

5. **Apply skill-defense-in-depth**: identify failure modes of the parallel agent strategy itself (e.g., agents finding duplicate issues, partial exploration, hallucinating problems) and design mitigations upfront.

---

## Phase 2 — /debug: Evidence-Based Root Cause Discovery

### Step 2A — Layer 1: 10 Parallel Exploration Agents

Dispatch 10 agents concurrently using **skill-dispatching-parallel-agents**. Each agent is scoped to a distinct codebase region. Suggested partition (adapt to actual repo structure):

| Agent | Scope |
|-------|-------|
| Agent-1 | Frontend: routing, pages, layouts |
| Agent-2 | Frontend: components, state management, hooks |
| Agent-3 | Frontend: API integration layer, data fetching, error handling |
| Agent-4 | Frontend: build config, bundler, environment vars, assets |
| Agent-5 | Backend: API routes, controllers, request/response handling |
| Agent-6 | Backend: business logic, services, domain models |
| Agent-7 | Backend: database layer, ORM queries, migrations, schemas |
| Agent-8 | Backend: auth, middleware, security, validation |
| Agent-9 | Gateway: proxying, rate limiting, load balancing, routing rules |
| Agent-10 | Shared: types, utilities, config, CI/CD, environment, tests |

Each agent MUST follow the official graphify three-step workflow for fast, focused codebase exploration. Do NOT manually walk directory trees or blindly read files — use the existing pre-built graph as the primary navigation layer.

> **The graphify graph is already built.** `graphify-out/graph.json` and `graphify-out/GRAPH_REPORT.md` already exist. Do NOT re-run graphify or re-build the graph. Go straight to querying it.
> If repeated structured queries are needed, the graph can be served as an MCP server:
> ```bash
> python -m graphify.serve graphify-out/graph.json
> # MCP config: { "mcpServers": { "graphify": { "type": "stdio", "command": ".venv/bin/python3", "args": ["-m", "graphify.serve", "graphify-out/graph.json"] } } }
> ```

**Step 1 — High-level overview:** Every agent MUST start by reading `graphify-out/GRAPH_REPORT.md`. This gives the god nodes (highest-degree concepts), surprising connections, and suggested questions the graph is uniquely positioned to answer. Do not skip this.

**Step 2 — Focused subgraph queries:** For each specific question about the agent's assigned scope, run a targeted `graphify query` instead of dumping the full graph. Examples of the query pattern (adapt the quoted string to the agent's actual scope):
```bash
graphify query "show the auth flow" --graph graphify-out/graph.json
graphify query "what connects DigestAuth to Response?" --graph graphify-out/graph.json
graphify query "show all API routes and their handlers" --graph graphify-out/graph.json
graphify query "what connects the frontend data layer to the backend?" --graph graphify-out/graph.json
graphify query "show the database query layer and ORM models" --graph graphify-out/graph.json
graphify path "NodeA" "NodeB" --graph graphify-out/graph.json   # shortest path between two nodes
graphify explain "ComponentName" --graph graphify-out/graph.json # plain-language explanation
```
The output includes node labels, edge types, confidence scores, source files, and source locations. Feed this focused output — NOT the raw `graph.json` — into the agent's analysis context.

**Step 3 — Targeted deep reads:** Only after consulting the graph, use `GitHub MCP` for deep reads of the specific files the graph identified as relevant to the agent's scope. Do not read files the graph did not surface.

Each agent MUST:
- Read `graphify-out/GRAPH_REPORT.md` first (high-level map)
- Run scope-appropriate `graphify query` commands to pull focused subgraphs
- Use `GitHub MCP` only for targeted deep reads of files surfaced by the graph
- Use `Context7` to look up framework-specific patterns and anti-patterns
- Write a **Memory File** (`memory_agent_{N}.md`) with:
  - Files explored (with paths)
  - Architecture summary of its scope
  - Observed patterns (good and bad)
  - Initial suspicions (flagged but not yet analyzed)
  - Raw notes — no filtering, capture everything

### Step 2B — Layer 2: Parallel Problem Analysis Agents (Dynamic Scale)

For each Memory File from Layer 1, dispatch a **parallel batch of analysis agents** using **skill-dispatching-parallel-agents**. All batches for all memory files run concurrently — do not wait for one file's batch to finish before starting another file's batch.

**Agent count per memory file: minimum 10, scale up as needed.** If a memory file covers a large or complex scope (e.g. the entire backend service layer), dispatch 15–20 analysts. Use judgment — the goal is exhaustive coverage, not a fixed number.

Each agent receives one Memory File and independently analyzes it for a specific problem category. Always dispatch at minimum these 10 categories in parallel:

| Analysis Agent | Problem Category |
|----------------|-----------------|
| Analyst-1 | Logic bugs and incorrect behavior |
| Analyst-2 | Security vulnerabilities (injection, auth bypass, exposure) |
| Analyst-3 | Performance bottlenecks (N+1, blocking I/O, memory leaks) |
| Analyst-4 | Data integrity and validation gaps |
| Analyst-5 | Error handling and resilience failures |
| Analyst-6 | Type safety and null safety issues |
| Analyst-7 | API contract inconsistencies (frontend/backend mismatch) |
| Analyst-8 | Dependency and version issues (outdated, vulnerable, conflicting) |
| Analyst-9 | Test coverage gaps and anti-patterns |
| Analyst-10 | Architectural violations and coupling problems |

Add more analyst roles as the scope demands (e.g. Analyst-11: concurrency/race conditions, Analyst-12: observability/logging gaps, etc.).

Each analyst MUST:
- Apply **skill-root-cause-tracing** to every issue found: trace back to the origin, not just the symptom
- Apply **skill-systematic-debugging** discipline: form a hypothesis, gather evidence, confirm or refute
- Write a **Problem Memory File** (`problem_agent_{scope}_{category}.md`) with:
  - Problem title (one sentence)
  - File path(s) and line number(s) affected
  - Root cause (not symptom)
  - Severity: Critical / High / Medium / Low
  - Reproduction steps or evidence
  - Confidence level (High / Medium / Low)

### Step 2C — Synthesis: Master Issues Plan

After all Problem Memory Files are written, a **Synthesis Agent** must:

1. De-duplicate issues (same root cause found by multiple analysts → merge)
2. Group by layer: Frontend / Backend / Gateway / Cross-cutting
3. Rank by severity and impact
4. Produce a **Master Issues Plan** (`master_issues_plan.md`) with:
   - Executive summary (top 10 critical issues)
   - Full categorized issue list with file references
   - Dependency map: which issues block which fixes
   - Estimated blast radius per issue

---

### Step 2D — Live Runtime Confirmation (BEFORE Entering Phase 3)

> **This step is mandatory.** Do not proceed to planning until real runtime evidence has been collected. Static code analysis alone is insufficient — the live system can reveal bugs that static analysis misses and disprove false positives from static analysis.

After `master_issues_plan.md` is written, **start all services locally and run parallel log-watching agents simultaneously with live testing** to confirm the issues are real and capture their exact runtime signatures.

#### 2D-1: Start All Services in Parallel

Launch every service concurrently (adapt commands to actual stack):

```bash
# All of these start IN PARALLEL — do not start one then wait
cd frontend  && npm run dev    > /.skill-workspace/logs/frontend.log  2>&1 &
cd backend   && npm run dev    > /.skill-workspace/logs/backend.log   2>&1 &
cd workers   && npm run start  > /.skill-workspace/logs/workers.log   2>&1 &
cd gateway   && npm start      > /.skill-workspace/logs/gateway.log   2>&1 &
docker compose up -d           > /.skill-workspace/logs/infra.log     2>&1 &

# Wait for all to be healthy before proceeding (skill-condition-based-waiting)
curl --retry 15 --retry-delay 2 --retry-connrefused http://localhost:3000/health
curl --retry 15 --retry-delay 2 --retry-connrefused http://localhost:8080/health
```

All log output is written to `/.skill-workspace/logs/` for agent consumption.

#### 2D-2: Dispatch Parallel Log-Watcher Agents

Immediately after services start, dispatch one **log-watcher agent per service** — all running concurrently. Each agent continuously tails its service's log file throughout all live testing in this step:

| Log-Watcher Agent | Service Log | Watching For |
|-------------------|------------|--------------|
| LogWatch-Frontend | `logs/frontend.log` | JS errors, build warnings, HMR failures, missing assets |
| LogWatch-Backend  | `logs/backend.log`  | Unhandled exceptions, 4xx/5xx, slow queries, crash traces |
| LogWatch-Workers  | `logs/workers.log`  | Job failures, queue errors, timeout exceptions |
| LogWatch-Gateway  | `logs/gateway.log`  | Routing errors, upstream failures, auth rejections |
| LogWatch-Infra    | `logs/infra.log`    | DB connection errors, migration failures, container crashes |

Add more log-watcher agents for any additional services present. Each log-watcher writes all anomalies it sees in real time to `/.skill-workspace/logs/watch_report_{service}.md`.

#### 2D-3: Live Validation Agents (Run in Parallel With Log-Watchers)

While log-watchers are running, dispatch parallel live-validation agents to exercise each suspected issue from `master_issues_plan.md`. Each validation agent targets one issue:

```bash
# Example validation commands — run all IN PARALLEL:
# Validate suspected auth issue
curl -v -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" -d '{"email":"test@example.com","password":"wrong"}'

# Validate suspected N+1 query issue
curl -v http://localhost:8080/api/list-endpoint

# Validate suspected frontend data-fetch error
# → trigger via Playwright MCP: navigate to page, observe network tab + console

# Validate suspected validation gap
curl -v -X POST http://localhost:8080/api/resource -d '{}' -H "Content-Type: application/json"
```

Each validation agent records: actual HTTP status, response body, response time, and any error observed.

#### 2D-4: Consolidate Runtime Evidence

After validation agents complete, stop all services. Each log-watcher agent writes its final report. A consolidation agent reads ALL watch reports and validation results and updates `master_issues_plan.md`:

- **Confirmed**: issue reproduced in runtime → mark with runtime evidence (log line, status code, trace)
- **Unconfirmed**: no runtime signal found → flag for manual review, do NOT remove from plan
- **New issues found**: runtime revealed something not in static analysis → add to plan immediately

> The updated `master_issues_plan.md` with runtime confirmation is the authoritative input to Phase 3. Do NOT start planning from the static-analysis-only version.

---

## Phase 3 — /plan: Deterministic Implementation Plan

### Step 3A — Layer 3: 10 Parallel Solution Research Agents

Dispatch 10 agents using **skill-dispatching-parallel-agents** to research solutions concurrently. Every agent MUST use **all three** research channels: **GitHub MCP**, **internet search**, and **internet deep research**. No agent is permitted to write its Solution Memory File until it has queried all three channels. Context7 is supplementary only.

| Research Agent | Research Domain |
|----------------|----------------|
| Research-1 | GitHub MCP + internet deep research: open-source fixes for top 3 critical issues |
| Research-2 | GitHub MCP + internet search: reference implementations for architecture violations |
| Research-3 | GitHub MCP + internet deep research: official docs + real-world examples for framework best practices |
| Research-4 | GitHub MCP + internet deep research: security hardening guides + CVE advisories for identified vulns |
| Research-5 | GitHub MCP + internet search: proven patterns and merged PRs for performance fixes found |
| Research-6 | GitHub MCP + internet search: battle-tested error handling + resilience patterns |
| Research-7 | GitHub MCP + internet deep research: test strategy patterns for coverage gaps |
| Research-8 | GitHub MCP + internet search: dependency upgrade migration guides + changelogs |
| Research-9 | Internet deep research: CVE databases, NVD, security advisories for identified issues |
| Research-10 | GitHub MCP + internet search: similar repos that have solved the same top issues |

**Research channel requirements (enforced per agent):**
- `GitHub MCP`: Search repos, read reference implementations, find merged PRs that solved analogous problems
- `Internet search`: Find blog posts, Stack Overflow answers, official announcements, and benchmarks
- `Internet deep research`: Full documentation sites, RFC/spec pages, in-depth technical writeups — not just top search results

Each research agent writes a **Solution Memory File** (`solution_agent_{N}.md`) with:
- Problem(s) addressed
- Proposed solution approach
- Reference implementations (with repo links)
- Trade-offs and risks
- Estimated implementation effort

### Step 3B — Master Implementation Plan

Synthesize all solution memory files using **skill-writing-plans** to produce `implementation_plan.md`:

```markdown
## Implementation Plan Structure

### 0. Pre-Flight Checklist
- [ ] All memory files reviewed and conflicts resolved
- [ ] Dependency order determined (fix A before B if B depends on A)
- [ ] Git branching strategy defined
- [ ] Rollback plan per critical fix documented

### 1. Fix Groups (in execution order)
Each fix must include:
  - Problem reference (from master_issues_plan.md)
  - Files to change
  - Specific changes (not vague — line-level where possible)
  - Verification step (how to confirm it worked)
  - Estimated risk

### 2. Testing Strategy
- Which existing tests must pass after each fix group
- New tests to write per fix
- Integration test checkpoints

### 3. Deployment Sequence
- Order of deployments (backend before frontend if breaking API change, etc.)
- Feature flags or dark launch requirements
- Monitoring alerts to watch post-deploy
```

Apply **skill-writing-plans** strictly: every step must be deterministic, verifiable, and reversible.

---

## ⛔ HARD IMPLEMENTATION GATE — READ BEFORE PHASE 4

**Implementation (Phase 4 /execute) is STRICTLY FORBIDDEN until ALL of the following are explicitly confirmed complete:**

```
GATE CHECKLIST — every item must be ✅ before writing a single line of fix code:

[ ] graphify-out/GRAPH_REPORT.md has been read and all Layer 1 agents have run their scope-appropriate graphify query commands against the existing graphify-out/graph.json
[ ] All exploration Memory Files are written (minimum 10, one per codebase region — scale higher if needed)
[ ] All Problem Memory Files are written (minimum 10 per exploration file, scaled to scope complexity)
[ ] master_issues_plan.md (static analysis version) is written, de-duplicated, and ranked
[ ] Step 2D completed: all services started locally, parallel log-watcher agents ran, live validation agents ran, master_issues_plan.md updated with runtime evidence and confirmed by user
[ ] All solution research agents have queried GitHub MCP + internet search + internet deep research (all three channels, no exceptions)
[ ] All Solution Memory Files are written
[ ] implementation_plan.md is fully written with every fix group, verification step, and rollback plan
[ ] implementation_plan.md has been shown to the user and confirmed
[ ] User has answered all verifying questions for Phase 4 (see Verifying Questions protocol below)
```

If ANY item above is not ✅, STOP. Do not proceed. Ask the user to confirm or supply the missing item.

---

## Verifying Questions Protocol (MANDATORY AT EVERY PHASE GATE)

Before transitioning from one phase to the next, the agent MUST pause and ask the user clarifying and confirming questions. Do not assume. Do not infer silently. Ask out loud and wait for answers.

**There is no limit on how many questions can be asked.** If something is unclear, ambiguous, or could go multiple ways, ask. It is better to ask 10 questions than to make one wrong assumption that corrupts the entire execution.

### Questions to ask at each gate:

**Before Phase 1 → Phase 2 (entering /debug):**
- What is the exact repo URL or local path to explore?
- Are there any layers, directories, or files that are OUT OF SCOPE for this analysis?
- Is `graphify` already installed and `graphify-out/graph.json` already generated, or should it be run fresh?
- Are there known issues you already want to make sure are included in the analysis?
- Are there any areas of the codebase that are especially sensitive (e.g., payment flows, auth)?

**Before Phase 2 → Phase 3 (entering /plan after seeing master_issues_plan.md):**
- Do the issues in `master_issues_plan.md` match your understanding of the codebase's problems?
- Are there any issues on the list that you want to EXCLUDE from fixing right now?
- Are there any issues NOT on the list that you know should be included?
- Do you agree with the severity rankings, or should any be adjusted?
- Are there deadline or scope constraints that affect which issues to prioritize?

**Before Phase 3 → Phase 4 (entering /execute after seeing implementation_plan.md):**
- Have you reviewed `implementation_plan.md` and is the fix order correct?
- Are there any fixes in the plan you want to DEFER or SKIP?
- Are there any external dependencies (team reviews, deployments, feature flags) that must happen before certain fixes?
- Do you want fixes done in parallel (git worktrees) or serially?
- Are there any environments (staging, prod) where fixes must be validated before merging?

**Before Phase 5 → Phase 6 (entering /verify):**
- Which specific user workflows must be tested in Phase 6?
- Is a staging/preview environment available for Playwright verification, or will testing be on localhost?
- Are there any workflows or endpoints that are explicitly OUT OF SCOPE for verification?

**At any point during execution if something unexpected is found:**
- STOP. Write a summary of the unexpected finding.
- Ask the user: "I found [X] which was not in the plan. Do you want me to: (a) add it to the plan and fix it, (b) log it as a deferred issue, or (c) ignore it?"
- Do NOT make autonomous decisions about scope changes.

---

## Phase 4 — /execute: Step-by-Step Implementation

Follow the implementation plan exactly. Use **skill-executing-plans** discipline:

1. **Never skip a verification checkpoint**
2. **Work in git branches** using **skill-using-git-worktrees** for parallel fix tracks:
   - One worktree per fix group to allow parallel safe implementation
   - Never modify main/trunk directly
3. **Apply skill-test-driven-development**: write the test that demonstrates the bug first, then fix the bug, then confirm the test passes
4. **Apply skill-testing-anti-patterns**: check that new tests are not brittle, tautological, or testing implementation details instead of behavior
5. After each fix group:
   - Run the existing test suite
   - Confirm no regressions
   - Stage for review

For each fix:
```
Step N: [Fix Title]
  → Files: [list]
  → Action: [specific change]
  → Verify: [command or check]
  → Status: [ ] pending / [x] done / [!] blocked
```

---

## Phase 5 — /review: Architecture & Code Quality Review

Before declaring any fix complete, apply **skill-requesting-code-review** and **skill-receiving-code-review**:

### Self-Review Checklist (per fix)
- [ ] Does the fix address the root cause (not just the symptom)?
- [ ] Does it introduce any new coupling or fragility?
- [ ] Is the solution consistent with the existing codebase patterns?
- [ ] Is error handling added/preserved correctly?
- [ ] Are types correct and null safety maintained?
- [ ] Are there any security implications?
- [ ] Is the fix reversible in production?

### Architecture Review
- Does the cumulative set of fixes improve or degrade the overall architecture?
- Are there any circular dependencies introduced?
- Does the final state match the intended design from /brainstorming?

Apply **skill-finishing-a-development-branch** before any branch is considered done (local only — do NOT push or merge to remote):
- All todos resolved
- No debug code left
- Changelog entry written locally
- Summary written: problem, solution, testing done, rollback plan

---

## Phase 6 — /verify: Live Local Verification (MANDATORY BEFORE DECLARING ANY FIX DONE)

Use **skill-verification-before-completion** and **production-live-verification**. All verification is done **locally** using full system access — running real services, real commands, real network calls. No mocking. No "it should work." Prove it works with live evidence.

> ⛔ Do NOT declare any fix complete based on code review or test output alone. You MUST run the services and verify live behavior.

---

### Step 6A — Start All Services + Parallel Log-Watcher Agents Simultaneously

Start all services and immediately dispatch log-watcher agents — both happen in parallel, not in sequence.

**Start all services in parallel:**
```bash
cd frontend  && npm run dev    > /.skill-workspace/logs/verify_frontend.log  2>&1 &
cd backend   && npm run dev    > /.skill-workspace/logs/verify_backend.log   2>&1 &
cd workers   && npm run start  > /.skill-workspace/logs/verify_workers.log   2>&1 &
cd gateway   && npm start      > /.skill-workspace/logs/verify_gateway.log   2>&1 &
docker compose up -d           > /.skill-workspace/logs/verify_infra.log     2>&1 &

# Health check all services before any test runs (skill-condition-based-waiting)
curl --retry 10 --retry-delay 2 --retry-connrefused http://localhost:3000/health
curl --retry 10 --retry-delay 2 --retry-connrefused http://localhost:8080/health
```

**Dispatch parallel log-watcher agents immediately (do not wait for testing to start):**

Each log-watcher runs for the ENTIRE duration of Phase 6 — from the moment services start until all verification is complete. They watch continuously so that any error triggered by any test is captured in real time with full stack traces.

| Log-Watcher Agent | Tailing | Captures |
|-------------------|---------|---------|
| LogWatch-Frontend | `verify_frontend.log` | JS errors, hydration failures, missing chunks, network errors |
| LogWatch-Backend  | `verify_backend.log`  | Exceptions, stack traces, slow query warnings, 5xx logs |
| LogWatch-Workers  | `verify_workers.log`  | Failed jobs, queue timeouts, unhandled rejections |
| LogWatch-Gateway  | `verify_gateway.log`  | Upstream errors, auth rejections, malformed routing |
| LogWatch-Infra    | `verify_infra.log`    | DB errors, connection pool exhaustion, container restarts |

Each log-watcher writes timestamped anomalies to `/.skill-workspace/logs/verify_watch_{service}.md` in real time. After all testing completes, these reports are the primary evidence for the sign-off blocks in Step 6E.

---

### Step 6B — System-Level curl / CLI Verification (Backend First)

For every fix implemented, MUST run direct system-level verification commands against the live local services. Use `curl`, CLI tools, and any available system access. Do not skip this step — this is the ground truth.

**For each fixed API endpoint or backend behavior:**
```bash
# Health and connectivity
curl -v http://localhost:8080/health

# Auth flows
curl -v -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass"}'

# Authenticated endpoints (use token from above)
curl -v http://localhost:8080/api/resource \
  -H "Authorization: Bearer <token>"

# Error handling — deliberately trigger error conditions
curl -v -X POST http://localhost:8080/api/resource \
  -H "Content-Type: application/json" \
  -d '{}'   # empty body — confirm proper 4xx and error message returned

# Data integrity — write then read back
curl -X POST http://localhost:8080/api/items -d '{"name":"test"}' -H "Content-Type: application/json"
curl http://localhost:8080/api/items   # confirm item appears

# Performance — confirm no hanging requests
time curl http://localhost:8080/api/slow-endpoint

# Security — confirm unauthorized access is rejected
curl -v http://localhost:8080/api/protected    # no token — must get 401
curl -v http://localhost:8080/api/admin -H "Authorization: Bearer <non-admin-token>"  # must get 403
```

For each command, record:
- HTTP status code received
- Response body (truncated if large)
- Response time
- Whether it matches the expected behavior after the fix

**Any unexpected status code, error body, or missing field = fix is NOT done. Go back to Phase 4.**

---

### Step 6C — Playwright Deep Frontend Verification (User Perspective)

After backend curl verification passes, use `Playwright MCP` for deep, realistic user-perspective testing. This is not smoke testing — exercise the actual user workflows end-to-end as a real user would.

**For every user-facing workflow affected by a fix:**

```
Workflow: [Name of workflow, e.g. "User Login and Dashboard Load"]
Steps:
  1. Navigate to http://localhost:3000
  2. [Exact UI actions — click, type, submit, wait for element]
  3. Assert: [What must be visible / present / absent]
  4. Assert: [Network request made — check request/response in DevTools via Playwright]
  5. Assert: [No console errors]
  6. Assert: [No failed network requests — no 4xx/5xx in network log]
```

Required Playwright checks for EVERY workflow:
- [ ] Page loads without JS errors in console
- [ ] No failed network requests (no red requests in network tab)
- [ ] All UI elements that should be visible ARE visible
- [ ] All UI elements that should be hidden ARE hidden
- [ ] Form submissions succeed and produce the correct UI feedback
- [ ] Error states render correctly (submit bad data → confirm error message shown)
- [ ] Auth-gated pages redirect unauthenticated users to login
- [ ] Data written via UI appears correctly on re-fetch / page reload
- [ ] Responsive behavior is not broken (test at multiple viewport sizes if relevant)

**Playwright must also intercept and verify network requests:**
```
- Confirm API calls go to the correct endpoints
- Confirm request payloads match expected schema
- Confirm response data is correctly rendered in the UI
- Confirm no extra/unexpected API calls are being made
```

---

### Step 6D — Regression Check

After verifying all fixed workflows, run through ALL major user workflows (not just the fixed ones) to confirm nothing was broken by the fixes:

```bash
# Re-run full test suite
npm test          # or: pytest / go test ./... / etc.

# Confirm no new failures
```

Then use Playwright to walk through at least the following (adapt to actual app):
- Homepage / landing page loads
- User registration (if applicable)
- User login / logout
- Core feature flows (the 2-3 most important things users do in the app)
- Any flows that share code paths with the fixed areas

---

### Step 6E — Verification Sign-Off

For each fix, fill in this sign-off block before marking complete:

```
Fix: [Title]
─────────────────────────────────────
curl verification:        [ ] PASSED  /  [ ] FAILED — [detail]
Log-watcher reports:      [ ] CLEAN   /  [ ] ERRORS — [paste anomaly from watch report]
Playwright workflow:      [ ] PASSED  /  [ ] FAILED — [detail]
No regressions:           [ ] PASSED  /  [ ] FAILED — [detail]
─────────────────────────────────────
Status: ✅ VERIFIED  /  ❌ NOT VERIFIED — return to Phase 4
```

**Do NOT proceed to issue tracking or completion until every fix has ✅ VERIFIED.**

---

### Step 6F — Issue Tracking

Only after all fixes are ✅ VERIFIED:

- Update `Linear MCP`: close resolved issues, log deferred items with priority and rationale
- Update `master_issues_plan.md`: mark each resolved issue with verification evidence

Do NOT declare completion until all Critical and High severity issues are ✅ VERIFIED and no new Critical/High issues were introduced.

---

## MCP Tool Usage Reference

| Tool | When to Use |
|------|-------------|
| `graphify` | FIRST step in every exploration — read GRAPH_REPORT.md, then run focused `graphify query` commands; optionally serve as MCP with `graphify.serve` |
| `GitHub MCP` | File reading, diff viewing, searching code, creating PRs, finding reference implementations |
| `Internet search` | Blog posts, Stack Overflow, announcements, quick solution lookups |
| `Internet deep research` | Full docs, RFCs, CVE advisories, in-depth technical writeups |
| `Context7` | Supplementary: library-specific API references when search/GitHub don't suffice |
| `Playwright MCP` | Live browser verification in Phase 6 |
| `Linear MCP` | Issue tracking, status updates, deferred items |

**All three research channels (GitHub MCP + internet search + internet deep research) are MANDATORY for every solution research agent.** No exceptions.

---

## Sub-Skills Orchestration Map

```
Phase 1 /brainstorming  →  skill-brainstorming, skill-defense-in-depth
Phase 2 /debug          →  skill-dispatching-parallel-agents, skill-root-cause-tracing,
                            skill-systematic-debugging, skill-subagent-driven-development
Phase 3 /plan           →  skill-dispatching-parallel-agents, skill-writing-plans,
                            skill-sharing-skills
Phase 4 /execute        →  skill-executing-plans, skill-using-git-worktrees,
                            skill-test-driven-development, skill-testing-anti-patterns,
                            skill-condition-based-waiting
Phase 5 /review         →  skill-requesting-code-review, skill-receiving-code-review,
                            skill-finishing-a-development-branch
Phase 6 /verify         →  skill-verification-before-completion, production-live-verification,
                            skill-testing-skills-with-subagents
```

---

## Memory File Naming Convention

```
memory_agent_{N}.md                          → Layer 1 exploration (N = 1 to however many regions exist)
problem_agent_{scope}_{category}.md         → Layer 2 analysis (min 10 per memory file, scale as needed)
solution_agent_{N}.md                       → Layer 3 research (N = 1 to however many are dispatched)
logs/watch_report_{service}.md              → Step 2D log-watcher output (one per service)
logs/verify_watch_{service}.md              → Phase 6 log-watcher output (one per service)
master_issues_plan.md                       → Synthesized issue list (updated after Step 2D)
implementation_plan.md                      → Final fix plan
```

Store all memory files in `/.skill-workspace/` at the root of the working directory.

---

## Guardrails & Anti-Patterns to Avoid

- **ALWAYS maximize parallel agent dispatch** — if two tasks have no data dependency between them, they MUST run concurrently; sequential execution of parallelizable work is a skill violation
- **Never skip /brainstorming** to jump straight to code — undiagnosed root causes produce patches, not fixes
- **Never merge problem discovery and solution writing** in the same agent pass — it biases analysis
- **Never trust a single agent's finding on Critical issues** — require cross-validation from at least one other analyst
- **Never implement without a written plan** — improvised fixes in complex codebases cascade into new bugs
- **Never declare "done" without Phase 6 live verification** — tests passing locally ≠ working in production
- **NEVER git push, git merge to remote, or open a PR** — all changes are local only; remote operations are strictly forbidden
- **Apply skill-condition-based-waiting** when agents produce outputs asynchronously — do not proceed to the next layer until all memory files from the current layer are complete and written

---

## Quick Reference: Parallel Agent Dispatch Template

When dispatching a layer of parallel agents, always specify:

```
Agent ID:       [unique identifier]
Scope:          [exact files/directories/concerns this agent is responsible for]
Input:          [memory files or repo paths it should read]
Output:         [exact file it must write, with path]
Constraints:    [what it must NOT do — e.g., must not modify code in this phase]
Success check:  [how to confirm the agent completed correctly]
```

---

## Completion Criteria

The skill is complete when:

- [ ] All exploration memory files written and reviewed (minimum 10, scaled as needed)
- [ ] All problem memory files written and de-duplicated (minimum 10 per exploration file, scaled as needed)
- [ ] `master_issues_plan.md` updated with Step 2D live runtime confirmation evidence and user-approved
- [ ] `implementation_plan.md` approved
- [ ] All Critical and High severity issues: implemented, reviewed, and verified
- [ ] No new Critical/High issues introduced by fixes
- [ ] All fixes have a ✅ VERIFIED sign-off block from Phase 6E (curl + log-watcher reports clean + Playwright)
- [ ] Linear issues updated
- [ ] Phase 6 live verification passed for all affected workflows
- [ ] ⛔ NO code has been pushed to any remote or merged to any branch on GitHub