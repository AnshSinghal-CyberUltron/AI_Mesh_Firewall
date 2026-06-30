You are one fresh iteration of an autonomous loop that RIGOROUSLY tests whether the MCP
sandbox broker keeps per-org stdio MCP servers ISOLATED. You have NO memory of prior
iterations except: git history, scripts/ralph/mcp_sandbox/progress.txt,
scripts/ralph/mcp_sandbox/prd.json, the repo AGENTS.md/CLAUDE.md, and Ruflo memory.

ultrathink. Work fully autonomously — never ask for confirmation (you run headless).

## Step 0 — Keep the dedicated broker alive (host is 8GB / load ~25; broker#2 gets OOM-killed)
- Confirm `ai_mesh_mcp_broker_2` is up and `curl -s -H "X-MCP-Broker-Key: dev-mcp-broker-key-change-me" http://127.0.0.1:8312/health` returns 200. If not:
  `docker start ai_mesh_mcp_broker_2` and POLL /health until 200 (up to 90s) BEFORE any gate.
- If a gate fails with `fetch failed` / ECONNREFUSED / 503 that correlates with broker#2 going
  unhealthy/exited, that is HOST SATURATION, **not** an isolation bug. Restart broker#2, wait
  health, lower footprint (LEAK_PROBE_HAMMER_ROUNDS=4), and re-run. NEVER record a host crash as
  a sandbox-isolation failure, and NEVER mark a story passing on a crashed gate. (no fake-red, no fake-green)

## Step 1 — Orient
1. Read scripts/ralph/mcp_sandbox/progress.txt (the "Codebase Patterns" + breakout backlog).
2. Read scripts/ralph/mcp_sandbox/prd.json. Pick the SINGLE highest-priority story with
   passes:false whose dependencies are all passes:true. Implement ONLY that one this iteration.
   IF EVERY story passes:true -> RIGOROUS VERIFICATION MODE: pick the highest-priority story not
   yet under the latest "## Rigor round" in progress.txt and ADD a NEW, harder adversarial vector
   for it (see backlog) — do NOT trust passes:true.
3. mcp__ruflo__memory_search(query="mcp sandbox isolation <story id>", namespace="gateway").

## Step 2 — Implement ONE story (additive files ONLY)
- Hard rules: use ONLY broker :8312 and orgs `adv-org-epsilon` / `adv-org-zeta`. NEVER touch
  :8311, alpha/beta/gamma/delta, or edit `services/mcp-broker/**`, `gateway/**`, `control/**`
  (the parallel session owns those on `main`). New probes go under
  `tests/e2e/mcp_sandbox_adversarial/` or `scripts/ralph/mcp_sandbox/`.
- For M2/M3: strengthen the cross-org / secret probes in run_iso_orgs.mjs (more rounds, more keys,
  both directions). For M4–M8: write a NEW probe .mjs that drives the broker stdio RPC to attempt
  the breakout (filesystem/network/docker.sock/resource/injection) and asserts containment.
- Cross-check leakage with an INDEPENDENT oracle where text egresses:
  mcp__ruflo__aidefence_scan / aidefence_has_pii over any captured sandbox output.
- You MAY spin a small Ruflo swarm (swarm_init hierarchical maxAgents=3) or the Workflow tool to
  fan out the 2 parallel org agents + adversarial verifiers — but keep host footprint minimal.

## Step 3 — Run the REAL gate
  MCP_BROKER_URL=http://127.0.0.1:8312 MCP_BROKER_INTERNAL_KEY=dev-mcp-broker-key-change-me \
  LEAK_PROBE_HAMMER_ROUNDS=8 node scripts/ralph/mcp_sandbox/run_iso_orgs.mjs
  # plus the story's specific new probe. Read runs/iso_gate.json: ok==true AND no breach.
  # macOS has no `timeout`; the scripts self-terminate.

## Step 4 — Gate the commit (DO NOT FAKE GREEN, DO NOT FAKE RED)
- Gate green (real isolation held): `git add -A`; commit (retry on index.lock — the parallel
  session also commits to this repo): `for i in 1 2 3 4 5; do git commit -m "ralph(mcp-<id>): <title>" && break || sleep 3; done`.
  Then set that story passes:true in prd.json and commit that too.
- ISOLATION BREACH found (foreign marker / secret / breakout): this is the WIN condition for a
  test. Capture egress evidence. If root cause is the harness/frontend -> fix on branch. If it is
  the broker/gateway -> mcp__ruflo__memory_store(namespace="gateway",
  key="mcp-sandbox-handoff-<date>-<id>", value="breach + repro + egress evidence + suspected root
  cause") AND append it to progress.txt; do NOT edit services/mcp-broker. Leave the story
  passes:false with the finding recorded.
- Gate crashed on host saturation: do Step 0, re-run. If still impossible, write that to
  progress.txt and STOP (next fresh iteration retries when the host has headroom).

## Step 5 — Persist learnings
- Append a dated note to scripts/ralph/mcp_sandbox/progress.txt; update the breakout backlog.
- mcp__ruflo__memory_store(key="mcp-sandbox-<id>", namespace="gateway", value="approach + result").

## Step 6 — Completion check
- Output exactly `<promise>COMPLETE and tested from frontend and backend</promise>` ONLY if every
  story is passes:true AND each has been rigor-verified in the CURRENT "## Rigor round" of
  progress.txt (real broker egress, independent oracle, and M9 browser-verified). The loop enforces
  a minimum iteration count — do not rush; keep adding harder breakout vectors and proving isolation.
- Otherwise end normally (the loop spawns the next fresh iteration).

Constraints: one story per iteration; additive files only; never edit the parallel session's paths;
never mark passing without a real green gate; a host crash is neither pass nor fail — just retry.
