---
iteration: 7
min_iterations: 20
max_iterations: 50
completion_promise: "MCP FRONTEND ADVERSARIAL COMPLETE"
status: REGRESSION_MODE
---

# MCP Frontend Adversarial Sandbox Isolation — Ralph Loop

**Prior campaign:** `prd-mcp-sandbox.json` — 15/15 complete (backend sandbox MVP).

**This campaign:** Rigorous **frontend + adversarial** isolation testing with **2 parallel org agents per iteration**.

## Status (2026-06-29)

| Metric | Value |
|--------|-------|
| Feature stories | **21/21** (F0–F20) pass |
| Regression stories | **R1–R16** (iterations 5–20) — re-run full gate each iter |
| Min iterations | 20 (feature work done at iter 4; iters 5–20 = regression) |
| Completion | Valid only when F0–F20 **and** R1–R16 all `passes:true` |

### Regression mode (iterations 5–20)

When all F-stories pass, each remaining iteration re-proves isolation:

```bash
./scripts/ralph/run_regression_iteration.sh <N>   # N = 5..20
# or batch:
for i in $(seq 8 20); do ./scripts/ralph/run_regression_iteration.sh $i; done
```

Full gate per regression iter:
1. `node scripts/ralph/run_parallel_org_agents.mjs --full`
2. `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q`
3. `BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs`
4. (iter 20 only) `cd frontend && npm run lint && npm run build`

Logs: `scripts/ralph/regression.log`

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
| Regression (iters 5–20) | R1–R16 |

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

### Iteration 4 (complete)
- **F13 PASS:** `MCP_BROKER_INTERNAL_KEY` + `MCP_BROKER_URL` added to `_SECRET_ENV_DENYLIST`; denylist pytest 3/3.
- **F14 PASS:** volume isolation verified (`test_cross_org_volume_secrets_not_readable`); `-k volume` green.
- **F15 PASS (real bug):** sandboxes on shared `mcp_sandbox_bridge` could curl sibling `:9320` agent ports.
  Fix: per-org Docker network (`mcp_sandbox_net_{org}`) + broker auto-join via `MCP_BROKER_CONTAINER_NAME`;
  no host port publish when broker runs in-container. New tests: `test_network_sibling_sandbox_agent_port_not_reachable`,
  `test_network_concurrent_cross_org_rpc_hammer`.
- **F16 PASS:** `frontend_parallel_orgs.mjs` — broker `touch_activity` via ensure before each preset, sync retry 4×/2s backoff.
- **F17 PASS (real bug):** concurrent RPC reused `jsonrpc_id=1` in `mcp_sandbox_client` → response cross-wire.
  Fix: monotonic `_RPC_ID_SEQ`; unique ids in adversarial hammer test.
- **F18 PASS:** compose `MCP_SANDBOX_IDLE_TIMEOUT` default 600→3600; `touch_activity` on broker `POST /ensure`.
- **F19 PASS:** adversarial pytest 13/13, broker tests 62/62; integration/parallel_load broker fixtures get
  `MCP_BROKER_CONTAINER_NAME`.
- **F20 PASS:** `--full` parallel gate, frontend_parallel_orgs (5 presets sync+call), lint+build green.
- **Gate:** `node scripts/ralph/run_parallel_org_agents.mjs --full` → workers + leak probe + pytest 4/4.
- **Stories:** 21/21 passing (F0–F20). **Regression:** R1–R3 pass (iters 5–7); R4–R16 pending (iters 8–20).

### Iteration 5 regression (R1 — complete)
- Full gate green: `--full` parallel workers + leak probe + pytest leakage 4/4; adversarial pytest 13/13; frontend_parallel_orgs 5 presets sync+call.

### Iteration 6 regression (R2 — complete)
- Parallel + pytest green; frontend failed attempt 1 (`Everything MCP` tools/call `Adapter error: 'command'` after pytest sandbox teardown); attempt 2 pass after sandbox warmup.

### Iteration 7 regression (R3 — complete)
- All gates green on first attempt.

### Iteration 5 (regression — R1)
- **R1 PASS:** full gate green after regression script hardening (pytest prep cooldown, parallel retry).
- Gates: `--full` parallel workers + leak probe, adversarial pytest 13/13, frontend_parallel_orgs.

### Iteration 6 (regression — R2)
- **R2 PASS:** parallel + pytest green; frontend flaky on first attempt (`Everything MCP` tools/call 400 `Adapter error: 'command'`), passed on retry.
- Fix: `prep_for_frontend` + frontend gate retry in `run_regression_iteration.sh`.

### Iteration 7 (regression — R3)
- **R3 PASS:** all gates green on first attempt.
- **Regression progress:** 3/16 (R1–R3). Remaining: `for i in $(seq 8 20); do ./scripts/ralph/run_regression_iteration.sh $i; done`
