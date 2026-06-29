---
iteration: 3
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: IN_PROGRESS
---

# MCP Frontend Adversarial Sandbox Isolation — Ralph Loop

**Prior campaign:** `prd-mcp-sandbox.json` — 15/15 complete (backend sandbox MVP).

**This campaign:** Rigorous **frontend + adversarial** isolation testing with **2 parallel org agents per iteration**.

## Requirements

| Rule | Value |
|------|-------|
| Min iterations | 20 |
| Max iterations | 50 |
| Completion promise | `MCP FRONTEND ADVERSARIAL COMPLETE` |
| Orgs per iteration | 2 (`adv-org-alpha`, `adv-org-beta`) |
| MCP servers per org | ≥5 stdio |
| Parallel gate | `node scripts/ralph/run_parallel_org_agents.mjs` |

## Architecture (unchanged from Phase 1)

- `mcp-broker` — Sandbox Controller; Docker socket on broker only.
- Per-org container (`ai_mesh.org_slug`); sandbox-agent on :9320 inside container.
- Gateway `MCP_STDIO_IN_PROCESS=false` → `mcp_sandbox_client.py` → broker.
- Frontend `MCPConnectorPanel` — stdio preset registration, tools/list sync, tools/call.

## Story backlog

**Source:** `scripts/ralph/prd-mcp-frontend-adversarial.json`

| Phase | Stories |
|-------|---------|
| Bootstrap | F0 |
| Per-org harness | F1, F2 |
| Parallel runner | F3, F8 |
| Adversarial pytest | F4, F5, F6 |
| Frontend Playwright | F7 |
| Fix placeholders | F9–F19 |
| Final gate | F20 |

## Quality gates

### Stack up
```bash
./scripts/ensure_mcp_sandbox_network.sh   # once, if network missing
docker compose --profile services up -d
curl -sf http://127.0.0.1:8311/health | jq -e '.docker_ok==true'
```

### Parallel org agents (EVERY iteration)
```bash
node scripts/ralph/run_parallel_org_agents.mjs
node scripts/ralph/run_parallel_org_agents.mjs --full   # + pytest leakage subset
```

### Adversarial pytest
```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q
```

### Frontend
```bash
cd frontend && npm run lint && npm run build
BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs
```

## Run the loop

```bash
chmod +x scripts/ralph/ralph-mcp-frontend-adversarial.sh
./scripts/ralph/ralph-mcp-frontend-adversarial.sh 50
```

## Iteration log

### Iteration 1 (complete)
- Created campaign artifacts (PRD F0–F20, adversarial tests, parallel runner, Ralph scripts).
- **F0–F4 pass:** bootstrap + 2×6 parallel org workers + denylist pytest.
- **Blocker:** `docker compose --profile services` fails on `mcp_sandbox_bridge` label conflict;
  mcp-broker started via `docker run` on `ai_mesh_firewall_default` + `mcp_sandbox_bridge`.
- **Fix applied:** `run_parallel_org_agents.mjs` — ensure body `{warm:true}`, agent health wait,
  correct stdio/rpc payload shape.
- **Next:** F5 leakage pytest, F7 frontend Playwright, gateway `MCP_STDIO_IN_PROCESS=false`.

### Iteration 2 (complete)
- **Compose fix:** `mcp_sandbox_bridge` declared `external: true`; added `scripts/ensure_mcp_sandbox_network.sh`.
- **Gateway:** recreated via compose with `MCP_STDIO_IN_PROCESS=false` + matching `MCP_BROKER_INTERNAL_KEY`.
- **F5 PASS:** leakage pytest 4/4 (`volume|foreign_org|npm_cache|denylist_secrets`).
- **F6 PASS:** escape pytest 4/4 (`traversal|proc_self|docker_sock|sibling`).
- **F7 PASS:** frontend_parallel_orgs.mjs — 5 stdio presets sync + tools/call (Playwright, Semgrep, Memory, Everything, Vibe Check).
- **MCP presets:** added Memory, Everything, Vibe Check stdio quick-register buttons.
- **Gate:** `node scripts/ralph/run_parallel_org_agents.mjs` green each iteration.
- **Stories:** 8/21 passing (F0–F7). Next: F8 `--full` runner, fix placeholders.

### Iteration 3 (complete)
- **F8 PASS:** `--full` gate fixed — pytest leakage subset now runs with `cwd=gateway/` (was repo root).
- **F9 PASS (real bug):** Sandbox read-only rootfs + non-root USER → npx failed writing `/home/sandbox/.npm`.
  Fix: `docker_manager.py` sets `NPM_CONFIG_CACHE=/tmp/.npm`, `UV_CACHE_DIR`, `XDG_CACHE_HOME` on container env.
- **Coverage expanded:** `cross_org_leak_probe.mjs` runs concurrently with 2 org workers (12 hammer rounds +
  cross-org marker + secret denylist probes). Broker/org_worker get 429/503 backoff-retry.
- **Frontend:** control-plane health wait (`/api/health/` not `/health/`), register/sync retry wrapper.
  Added Filesystem MCP + Fetch MCP presets to MCPConnectorPanel (Fetch uses `mcp-server-fetch`).
- **F10–F12 PASS:** full adversarial pytest 11/11, frontend_parallel_orgs green, broker tests 59/59.
- **Gate:** `node scripts/ralph/run_parallel_org_agents.mjs --full` → workers + leak probe + pytest 4/4.
- **Stories:** 13/21 passing (F0–F12). Next: F13–F19 fix placeholders, F20 final gate.
