# Cursor Ralph Loop — MCP Frontend Adversarial

One-page guide for running the **MCP frontend adversarial** campaign inside **Cursor Agent**, not Claude Code CLI.

## Three different things (don't mix them up)

| What | Where | Who runs it |
|------|-------|-------------|
| **Cursor Ralph Loop** | `.cursor/ralph/scratchpad.md` + `~/.cursor/plugins/local/ralph-loop/` | **You** in Cursor chat — this is what you want |
| **Claude CLI bash loops** | `scripts/ralph/ralph.sh`, `ralph-mcp-frontend-adversarial.sh` | Headless `claude --print` — **not Cursor** |
| **Test gates** | `run_parallel_org_agents.mjs`, pytest, Playwright, `run_regression_iteration.sh` | Shell scripts hitting **local Docker** — the agent runs these; they are not an AI loop |

The **AI loop** = Cursor Agent reads the scratchpad, runs gates, fixes code, marks PRD stories, repeats. The **test scripts** only verify the local stack (gateway, broker, frontend, sandboxes). No external cloud APIs.

## How to activate

1. Install the Ralph Loop plugin (local: `~/.cursor/plugins/local/ralph-loop/`).
2. Ensure `.cursor/ralph/scratchpad.md` exists with frontmatter (`iteration`, `min_iterations`, `max_iterations`, `completion_promise`).
3. In Cursor chat, say something like:

   > Continue the Ralph loop for MCP frontend adversarial. Read `.cursor/ralph/scratchpad.md` and follow it.

4. After each agent turn, the **stop hook** re-feeds the scratchpad prompt until the agent outputs `<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>` or `max_iterations` is hit.

You can also nudge manually: **"continue Ralph loop"** or **"next iteration"**.

## Prerequisites

```bash
cd /path/to/AI_Mesh_Firewall
./scripts/ensure_mcp_sandbox_network.sh 2>/dev/null || true
docker compose --profile services up -d
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
```

## What one iteration does

1. Read `scripts/ralph/prd-mcp-frontend-adversarial.json` — work the first `passes:false` story (R1–R16).
2. Run gates in order (~15–25 min):
   - `make mcp-adversarial-gate` **or** the three commands in the scratchpad
   - On R16: also `cd frontend && npm run lint && npm run build`
3. Fix failures in code (not tests).
4. Mark story `passes:true`, commit locally.
5. Output completion promise only when **all** stories pass **and** iteration ≥ 20.

## Makefile

| Target | Purpose |
|--------|---------|
| `make mcp-adversarial-gate` | Run parallel org agents + adversarial pytest + frontend e2e once |
| `make mcp-adversarial-reset-prd` | Reset R1–R16 to `passes:false` in PRD |
| `make mcp-adversarial-reset` | Kill regression batches, clear locks (heavy reset) |

## Fresh start

```bash
make mcp-adversarial-reset-prd
# Edit .cursor/ralph/scratchpad.md: iteration: 1, status: ACTIVE
```

## Terminal-only alternative

If you prefer **no Cursor agent** and a shell loop instead, see `scripts/ralph/START_IN_TERMINAL.md`. That runs `run_logical_iteration.sh` in your Terminal — still not Claude CLI, but also not the Cursor Ralph plugin.

## Completion

When F0–F20 and R1–R16 are all `passes:true` and ≥20 iterations:

```
<promise>MCP FRONTEND ADVERSARIAL COMPLETE</promise>
```

The Ralph stop hook clears the scratchpad and stops the loop.
