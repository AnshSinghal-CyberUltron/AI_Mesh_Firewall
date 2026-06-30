---
iteration: 1
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: ACTIVE
active_story: R1-regression-iter5
prd: scripts/ralph/prd-mcp-frontend-adversarial.json
note: Cursor Ralph Loop — NOT Claude CLI. FULL RESET 2026-06-30.
---

# MCP Frontend Adversarial — Cursor Ralph Loop

You are one **Cursor Agent** iteration of the Ralph loop. The **stop hook** in `~/.cursor/plugins/local/ralph-loop/` re-feeds this prompt after each turn until you output the completion promise.

## DO NOT USE

- `claude` CLI or `claude --print`
- `scripts/ralph/ralph.sh`, `ralph-mcp-frontend-adversarial.sh`, or any bash loop that spawns Claude Code headless
- `./scripts/ralph/run_logical_iteration.sh` as a substitute for doing the work yourself — that script is for **Terminal operators** only; **you** run the gates directly in this Cursor session

## What you ARE

The **AI loop**. Test gates (pytest, Node, Playwright) hit the **local Docker stack** only — gateway :8300, mcp-broker :8311, frontend :8180, per-org sandboxes. Fix code when gates fail. Repeat until done.

---

## Campaign status

| Metric | Value |
|--------|-------|
| Feature stories F0–F20 | all `passes:true` (implementation done) |
| Regression stories R1–R16 | `passes:false` — **your work** |
| Min iterations | 20 (scratchpad `iteration` frontmatter) |
| Max iterations | 50 |
| Completion | Only when F0–F20 **and** R1–R16 all `passes:true` **and** `iteration` ≥ 20 |

### Iteration map (logical → PRD)

| Scratchpad `iteration` | PRD story | Extra gate |
|------------------------|-----------|------------|
| 1 | R1-regression-iter5 | — |
| 2 | R2 | — |
| … | … | … |
| 16 | R16-regression-iter20 | `cd frontend && npm run lint && npm run build` |
| 17–20 | (no PRD mark) | re-run full gates for min-20 proof |
| 21+ | — | only if still failing stories |

---

## EVERY iteration — follow exactly

### 0. Orient

1. Read `scripts/ralph/prd-mcp-frontend-adversarial.json`.
2. Pick the **first** story with `passes:false` whose `dependencies` are all `passes:true`. Prefer **R** stories over **F** (F should already pass).
3. Note scratchpad `iteration` from frontmatter — bump `active_story` in frontmatter when you start a story.

### 1. Ensure stack is up (once per session if unsure)

```bash
./scripts/ensure_mcp_sandbox_network.sh 2>/dev/null || true
docker compose --profile services up -d
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
```

### 2. Run gates IN ORDER (or `make mcp-adversarial-gate`)

```bash
# Gate 1 — 2 parallel org agents (adv-org-alpha + adv-org-beta), 6 stdio MCP servers each
node scripts/ralph/run_parallel_org_agents.mjs --full

# Gate 2 — backend adversarial pytest (Docker sandboxes, leakage, escape, denylist)
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q

# Gate 3 — frontend Playwright (MCPConnectorPanel, 5+ presets per org)
BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
```

**On R16 only** (or any frontend lint/build story), also run:

```bash
cd frontend && npm run lint && npm run build
```

Gates take **~15–25 minutes** total. Run them in the foreground; one iteration per Cursor turn is expected.

### 3. Fix failures

- Fix **production code**, not tests (never weaken assertions to get green).
- Security invariants: egress bytes are truth; fail closed on no-op scrub; cross-org isolation must hold.
- Re-run failed gates until all green.

### 4. Mark progress

When **all gates for this iteration are green**:

1. Set the matching R-story `passes:true` in `scripts/ralph/prd-mcp-frontend-adversarial.json` (use `jq` or precise edit).
2. Commit locally: `git add -A && git commit -m "ralph(<story-id>): regression gate green"`
3. Append a one-line note to the **Iteration log** section below.

### 5. Completion check

Output **only** when **all** of these are true:

- Every story in `prd-mcp-frontend-adversarial.json` has `passes:true`
- Scratchpad frontmatter `iteration` ≥ `min_iterations` (20)
- You just ran the full gate suite and it passed

Then and only then output exactly:

```
<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>
```

If stories remain `passes:false` or `iteration` < 20, **do not** output the promise. End normally; the stop hook will feed this prompt again.

---

## Architecture (quick reference)

- **mcp-broker** — Sandbox Controller; Docker socket on broker only.
- Per-org container (`ai_mesh.org_slug`); sandbox-agent on :9320 inside container.
- Gateway `MCP_STDIO_IN_PROCESS=false` → `mcp_sandbox_client.py` → broker.
- Frontend **MCPConnectorPanel** — stdio preset registration, tools/list sync, tools/call.

## Makefile shortcuts

```bash
make mcp-adversarial-gate          # run all 3 gates once
make mcp-adversarial-reset-prd     # reset R1–R16 to passes:false
make mcp-adversarial-reset         # kill batches, locks, archive log (Terminal ops)
```

See `scripts/ralph/README-CURSOR-RALPH.md` for operator guide.

---

## Iteration log

### FULL RESET 2026-06-30
- Cursor Ralph Loop configured; R1–R16 `passes:false`; F0–F20 unchanged.
- **Next story:** R1-regression-iter5
