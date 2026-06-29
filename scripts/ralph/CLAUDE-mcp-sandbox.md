You are one fresh iteration of the MCP per-tenant Docker sandbox Ralph loop.
You have NO memory of prior iterations except: git history, scripts/ralph/progress.txt,
scripts/ralph/prd-mcp-sandbox.json, .skill-workspace/implementation_plan.md, and repo AGENTS.md.

ultrathink. Work fully autonomously — never ask for confirmation (you run headless).

## Step 1 — Orient
1. Read scripts/ralph/progress.txt (MCP Sandbox section + Codebase Patterns).
2. Read scripts/ralph/prd-mcp-sandbox.json. Pick the SINGLE highest-priority story with passes:false
   whose dependencies are all passes:true. Implement ONLY that one story.
3. Source of truth: .skill-workspace/implementation_plan.md

## Step 2 — Implement that ONE story
- Sandbox image: services/mcp-broker/sandbox-image/
- Broker controller: services/mcp-broker/src/sandbox/
- Gateway client: gateway/ai_mesh_gateway/mcp_sandbox_client.py
- MCP_STDIO_IN_PROCESS=true for dev fallback; prod uses Docker sandboxes via mcp-broker
- No npm package restrictions; full outbound network in sandboxes
- Security: never pass gateway secrets to sandboxes; scan/audit stays in mcp_proxy.py

## Step 3 — Run the REAL quality gate
Backend: cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q
Broker: pytest services/mcp-broker/tests -q (from repo root or broker dir)
Frontend (E2 story): cd frontend && npm run lint && npm run build
Docker integration: MCP_STDIO_IN_PROCESS=false with real Docker

## Step 4 — Gate the commit
- If gate FAILS: fix and re-run. If blocked, commit WIP, note in progress.txt, STOP.
- If gate PASSES: git add -A && git commit -m "ralph(<story-id>): <title>"
  Set passes:true in prd-mcp-sandbox.json and commit.

## Step 5 — Persist learnings
Append dated note to scripts/ralph/progress.txt under ## MCP Sandbox Ralph Loop.

## Step 6 — Completion
If EVERY story in prd-mcp-sandbox.json has passes:true AND all gates pass, output exactly:
<promise>COMPLETE and tested from frontend and backend</promise>
Otherwise end normally.

Constraints: one story per iteration; never mark passing without green gate; minimal diffs.
