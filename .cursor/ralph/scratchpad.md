# Cursor Ralph — Chat Pipeline FREEZE (3rd session; do NOT disturb the running MCP + Claude sessions)

**Worktree:** `/home/contact_cyberultron_com/amf-pipeline` (`cursor/chat-pipeline-freeze`)  
**Hive:** `hive-1782976205971-u1gav1`

## Coordination (every iteration)
- [x] C0. Own ONLY: enforcement.py, policy_engine.py, bedrock_scanner.py, output_guard.py,
      llm_router.py, tests/golden/, docs/pipeline/. NEVER touch mcp_*/frontend/**.
      main.py P3b edit done; no further main.py edits.

## P1 — Characterize (before fixing)
- [x] 1. Run 9 use cases → capture real stages[] → docs/pipeline/CURRENT_BEHAVIOR.md.
      **Iter 3:** live characterization for 03–09; case 02 unit phi_policy; CURRENT_BEHAVIOR updated.
- [x] 2. CHAT_PIPELINE_CONTRACT.md
- [x] 3. TRACE_UI_CONTRACT.md

## P2 — Golden suite + CI gate
- [x] 4. tests/golden/ — live_driver + 9 blessed snapshots; xfails removed.
- [x] 5. `.github/workflows/chat-pipeline-golden.yml`

## P3 — Fix B-ENF
- [x] 6a. enforcement.py + unit tests
- [x] 6b. main.py resolve_enforcement() swap (P3b)

## P4 — Fix B-POL
- [x] 7. Redis seed fix (iter 2) + ReDoS false-positive fix in policy_engine.py (iter 3)

## P5 — Freeze routing/kill-switch/output-guard
- [x] 8. Cases 07–09 green live; no output_guard.py / llm_router.py edits needed.

## P6 — Verify + hold
- [x] 9. Live gate 3× green (23 passed each run).
- [ ] CI job with live stack (optional follow-up); offline gate 16 passed / 7 skipped.

## Iteration 3 evidence
- `GATEWAY_LIVE=1 pytest ai_mesh_gateway/tests/test_enforcement.py tests/golden -q` → **23 passed** (×3)
- `GATEWAY_LIVE=0` → **16 passed, 7 skipped**
- **Blocker (deploy):** live gateway container lacks policy_engine ReDoS fix — case 02 chat path still flags; golden uses unit path.
