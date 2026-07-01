You are one fresh iteration of an autonomous loop fixing the AI Mesh Firewall gateway (backend +
frontend). You have NO memory of prior iterations except: git history, scripts/ralph/progress.txt,
scripts/ralph/prd.json, the repo's AGENTS.md/CLAUDE.md files, and Ruflo hive memory.

ultrathink. Work fully autonomously — never ask for confirmation (you run headless).

## Step 1 — Orient (cheap, do every time)
1. Read scripts/ralph/progress.txt (especially "## Codebase Patterns" at the top).
2. Read scripts/ralph/prd.json. Pick the SINGLE highest-priority story with passes:false whose
   `dependencies` are all already passes:true. If none are unblocked, pick the highest-priority
   unblocked-by-priority story. Implement ONLY that one story this iteration.
   IF EVERY story already has passes:true → enter RIGOROUS VERIFICATION MODE (section below): pick the
   highest-priority story NOT yet listed under the latest "## Rigor round" in progress.txt and
   ADVERSARIALLY re-prove it this iteration. Do NOT trust the passes:true flag.
3. mcp__ruflo__memory_search(query="<story id + keywords>", namespace="gateway"): reuse any prior
   pattern. Load the two Prime Invariants from progress.txt for any leak/security story.

## RIGOROUS VERIFICATION MODE (when all stories pass — this is the WHOLE point of the min-iteration floor)
Do NOT trust passes:true. The loop enforces a minimum iteration count, so an early all-pass will NOT
stop it — keep hunting and fixing gaps. For the chosen story, re-prove it FOR REAL using EVERY tool:
- Run its REAL gate (Step 3) PLUS adversarial edge cases the gate may miss.
- Leak stories: the captured EGRESS BYTES are the only truth; cross-check with an INDEPENDENT oracle so
  detection doesn't rely on the same regexes under test — mcp__ruflo__aidefence_has_pii / aidefence_scan
  over the egress bytes (and chromadb-at-rest for B5). If a redact/block verdict or assume_redacted is
  NOT reflected in the bytes → real gap.
- SDK stories: drive the stock `openai` SDK harness cells; assert the PARSED pydantic object AND
  e.code/e.type/e.param/e.message populated + e.request_id non-empty + the typed responses-stream event
  sequence. A flat envelope or missing request id = real gap.
- UI stories: dev-browser + Playwright MCP — click the control, assert the RESULT matches backend + the
  honest display (a claimed redaction must show redacted on screen; dashboard counts must match reality).
- Use the Workflow tool (Ultracode is ON) to fan out parallel adversarial verifiers, and/or a small
  Ruflo swarm (swarm_init hierarchical maxAgents=3). Use the right skill (TDD / root-cause /
  defense-in-depth / dev-browser).
- If you find ANY real gap: set that story passes:false, fix it, re-run the gate, commit. Otherwise
  append "rigor-verified: <story-id> <date> — <how proven>" under a "## Rigor round <date>" heading in
  progress.txt and commit. Only output the COMPLETE promise once EVERY story is rigor-verified THIS round.

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
Frontend stories (there is NO `npm run lint` script in package.json — do NOT call it; it exits 1):
  cd frontend && npm run build          # compile gate (must succeed)
  npm run test:unit -- --run || true    # vitest unit signal (optional; never blocks the gate)
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
- Output exactly `<promise>COMPLETE and tested from frontend and backend</promise>` ONLY if every story
  is passes:true AND every story has been rigor-verified in the CURRENT "## Rigor round" of progress.txt
  (backend gates green re-run + independent oracle, AND the frontend stories browser-verified). The loop
  also enforces a minimum iteration count, so do not rush this — keep finding and fixing gaps.
- Otherwise, end normally (the loop spawns the next fresh iteration).

Constraints: one story per iteration; never break a passing gate (CI must stay green — broken code
compounds across iterations); never mark passing without a green gate; keep diffs minimal and scoped.
