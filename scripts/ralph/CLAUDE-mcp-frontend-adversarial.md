You are one fresh iteration of the **MCP frontend adversarial sandbox isolation** Ralph loop.
You have NO memory of prior iterations except: git history, scripts/ralph/progress.txt,
scripts/ralph/prd-mcp-frontend-adversarial.json, .cursor/ralph/scratchpad.md, and repo AGENTS.md.

ultrathink. Work fully autonomously — never ask for confirmation (you run headless).

## Campaign rules (NON-NEGOTIABLE)

1. **Minimum 20 iterations** before completion promise is valid; max 50.
2. **Every iteration MUST run 2 parallel org agents** before marking ANY story `passes:true`:
   ```bash
   node scripts/ralph/run_parallel_org_agents.mjs
   ```
3. Frontend stories MUST verify via Playwright/browser (MCPConnectorPanel), not API-only.
4. Use all available tools: ruflo MCP, Playwright MCP, browser skills, gateway venv pytest.
5. Security: egress bytes = truth; never inject gateway/broker secrets into sandboxes.

## Step 1 — Orient
1. Read `.cursor/ralph/scratchpad.md` and `scripts/ralph/progress.txt` (MCP Frontend Adversarial section).
2. Read `scripts/ralph/prd-mcp-frontend-adversarial.json`. Pick the SINGLE highest-priority story
   with `passes:false` whose dependencies are all `passes:true`.
3. If F0 not passing, start with F0-bootstrap.
4. **Regression mode (iterations 5–20):** When F0–F20 all have `passes:true`, do NOT emit the
   completion promise until R1–R16 also pass. Pick the lowest-numbered R-story with `passes:false`
   (R1 = iteration 5 … R16 = iteration 20). Each R-story runs the **full gate** even when no new
   feature work is needed:
   ```bash
   ./scripts/ralph/run_regression_iteration.sh <iteration_number>
   ```
   Or manually: `--full` parallel org agents, full adversarial pytest, `frontend_parallel_orgs.mjs`.

## Step 2 — Parallel gate FIRST (before marking pass)
```bash
# Always run before flipping any story to passes:true:
node scripts/ralph/run_parallel_org_agents.mjs
# For F5/F6/F20 also:
node scripts/ralph/run_parallel_org_agents.mjs --full
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
# Frontend stories:
cd frontend && npm run lint && npm run build
BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
```

## Step 3 — Implement that ONE story
- Orgs: `adv-org-alpha`, `adv-org-beta` — each needs **≥5 MCP stdio servers**.
- Adversarial probes: GATEWAY_INTERNAL_API_KEY, cross-org volume paths, docker.sock, /proc.
- Fix stories F9-F19: rename placeholder when real failure found; minimal diffs.

## Step 4 — Gate the commit
- If gate FAILS: fix and re-run parallel agents. If blocked, WIP commit + progress.txt, STOP.
- If gate PASSES:
  ```bash
  git add -A && git commit -m "ralph(<story-id>): <title>"
  ```
  Set `passes:true` in prd-mcp-frontend-adversarial.json and commit.

## Step 5 — Persist learnings
Append dated note to `scripts/ralph/progress.txt` under `## MCP Frontend Adversarial Ralph Loop`.

## Step 6 — Completion
If EVERY story (F0–F20 **and** R1–R16) in prd-mcp-frontend-adversarial.json has `passes:true` AND
at least **20 iterations** have been logged in progress.txt AND:
- `node scripts/ralph/run_parallel_org_agents.mjs --full` exits 0
- adversarial pytest + frontend_parallel_orgs.mjs green

output exactly:
<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>

Otherwise end normally (regression iterations 5–20 may still be pending).

Constraints: one story per iteration; never mark passing without green parallel-org gate; minimal diffs.
