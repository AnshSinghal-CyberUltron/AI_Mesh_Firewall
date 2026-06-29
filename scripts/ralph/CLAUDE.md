You are one fresh iteration of an autonomous loop fixing the AI Mesh Firewall gateway (backend +
frontend). You have NO memory of prior iterations except: git history, scripts/ralph/progress.txt,
scripts/ralph/prd.json, the repo's AGENTS.md/CLAUDE.md files, and Ruflo hive memory.

ultrathink. Work fully autonomously — never ask for confirmation (you run headless).

## Step 1 — Orient (cheap, do every time)
1. Read scripts/ralph/progress.txt (especially "## Codebase Patterns" at the top).
2. Read scripts/ralph/prd.json. Pick the SINGLE highest-priority story with passes:false whose
   `dependencies` are all already passes:true. If none are unblocked, pick the highest-priority
   unblocked-by-priority story. Implement ONLY that one story this iteration.
3. mcp__ruflo__memory_search(query="<story id + keywords>", namespace="gateway"): reuse any prior
   pattern. Load the two Prime Invariants from progress.txt for any leak/security story.

## Step 2 — Implement that ONE story (small, focused)
- Use the right skill: dev-browser for UI stories; skill-test-driven-development + skill-root-cause-
  tracing + skill-defense-in-depth for backend; reuse responses_adapters.py helpers
  (coerce_chat_error_to_openai / build_openai_error) for SDK error work.
- For complex stories you MAY spin a small Ruflo swarm (swarm_init hierarchical maxAgents=3) to
  parallelize, but keep the change scoped to this story.
- SECURITY INVARIANTS (non-negotiable on any leak story): the bytes egressed to a provider/embedding/
  vector store are the only source of truth; an action label (redact/block) or assume_redacted flag
  is valid ONLY if the captured bytes reflect it. Never report redact while forwarding raw; if the
  scrubber is a no-op on a flagged span, fail closed.

## Step 3 — Run the REAL quality gate for that story (this repo's commands)
Backend stories (USE THE GATEWAY VENV — plain `python` lacks the deps and will false-fail):
  cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q
  # plus the story's specific suite, e.g.:
  #   ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_openai_sdk_compat.py -q   (SDK stories)
  #   ./.venv/bin/python -m pytest tests/leakhunt -q   (leak stories — BUILD the A2 suite under
  #       gateway/tests/leakhunt so this gate path resolves; repo-root tests/leakhunt/capture_addon.py
  #       is a separate mitmproxy addon, NOT this pytest suite)
  ./.venv/bin/ruff check ai_mesh_gateway 2>/dev/null || ruff check ai_mesh_gateway || true
Frontend stories:
  cd frontend && npm run lint && npm run build
  # UI behavior: "Verify in browser using the dev-browser skill" against the running Vite app —
  # click the relevant controls, assert the result, screenshot.

## Step 4 — Gate the commit (DO NOT FAKE GREEN)
- If the gate FAILS: fix and re-run within this iteration. If you cannot get it green, do NOT mark
  the story passing; commit any safe WIP, write what blocked you to progress.txt, and STOP (the next
  fresh iteration retries). A story is passes:true ONLY when its gate is actually green.
- If the gate PASSES:
   git add -A && git commit -m "ralph(<story-id>): <title>"
   Set that story's "passes": true in scripts/ralph/prd.json (use jq or a precise edit) and commit that too.

## Step 5 — Persist learnings (this is how future iterations get smarter)
- Append a dated note to scripts/ralph/progress.txt. If you found a general, reusable pattern, add it
  to the "## Codebase Patterns" section at the TOP of progress.txt.
- Update the nearest AGENTS.md (or create one) in any directory you changed with conventions/gotchas.
- mcp__ruflo__memory_store(key="<story-id>", namespace="gateway", value="approach + gotchas");
  mcp__ruflo__neural_train(<trajectory>).

## Step 6 — Completion check
- If EVERY story in prd.json now has passes:true, output exactly: <promise>COMPLETE and tested from frontend and backend</promise>
- Otherwise, end normally (the loop spawns the next fresh iteration).

Constraints: one story per iteration; never break a passing gate (CI must stay green — broken code
compounds across iterations); never mark passing without a green gate; keep diffs minimal and scoped.
