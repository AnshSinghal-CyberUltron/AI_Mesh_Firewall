# Cursor Ralph — Chat Pipeline FREEZE (3rd session; do NOT disturb the running MCP + Claude sessions)

**Worktree:** `/home/contact_cyberultron_com/amf-pipeline` (`cursor/chat-pipeline-freeze`)  
**Hive:** `hive-1782976205971-u1gav1` (join attempted; npx ENOTEMPTY — using existing hive ID from PARALLEL_CLAIMS)

## Coordination (every iteration)
- [x] C0. Join hive. Own ONLY: enforcement.py(new), policy_engine.py, bedrock_scanner.py, output_guard.py,
      llm_router.py, tests/golden/, docs/pipeline/. NEVER touch mcp_*/sandbox/broker or frontend/**.
      For the ONE main.py edit (step 6), claim main.py:3500-6600 in mcp-parallel/claims first; if held, skip.
      **Evidence 2026-07-02:** read mcp-parallel/claims — no active main.py claim.

## P1 — Characterize (before fixing)
- [x] 1. Run the 9 use cases locally → capture real stages[] → docs/pipeline/CURRENT_BEHAVIOR.md.
      **Unit path done;** cases 2–9 live docker pending. CURRENT_BEHAVIOR.md written.
- [x] 2. Write docs/pipeline/CHAT_PIPELINE_CONTRACT.md (stage semantics + enforcement precedence table).
- [x] 3. Write docs/pipeline/TRACE_UI_CONTRACT.md (stages[] shape) — HANDOFF for frontend session.

## P2 — Golden suite + CI gate (land EARLY)
- [x] 4. tests/golden/ drives real pipeline via OpenAI SDK; snapshot normalized stages[] for 9 cases.
      **Scaffold landed:** `gateway/tests/golden/test_chat_pipeline_golden.py` + case 01 snapshot;
      cases 2–9 xfail/pending live gate.
- [x] 5. Wire CI gate: chat-pipeline snapshot can't change without deliberate re-bless.
      **File:** `.github/workflows/chat-pipeline-golden.yml`

## P3 — Fix B-ENF
- [x] 6a. Create enforcement.py + exhaustive unit tests.
      **Files:** `gateway/ai_mesh_gateway/enforcement.py`, `tests/test_enforcement.py`
- [x] 6b. ONE main.py edit: resolve_enforcement() call-site swap (claimed region only).
      **Evidence 2026-07-02:** claim `cursor-chat-pipeline-freeze-P3b`; swapped input-scan
      enforcement in main.py:6161–6350 to use `resolve_enforcement()` + `should_hard_block` /
      `should_apply_redaction`. Gates: 15 passed, 8 xfailed.

## P4 — Fix B-POL
- [x] 7. Diagnose PKG2_PIPE_PII/PCI/PHI compile+push; fix policy_engine.py.
      **Live root cause:** Redis had no compiled bundles (policy_count=0). Seeding via
      `seed_policy_package --org-slug zeroshield` pushed 45 policies; live `/v1/policy/check`
      returns matched_rules for SSN+email. **No policy_engine.py change needed.**

## P5 — Freeze routing/kill-switch/output-guard
- [ ] 8. Cases 6/7/8/9 pinned by golden tests; fix output_guard.py/llm_router.py only if red.

## P6 — Verify + hold
- [ ] 9. All 9 golden cases green 3×; CI gate active; re-characterize matches contract.

## Iteration 1 evidence
- Gateway venv created at `gateway/.venv` (minimal pytest deps; full editable install blocked).
- `pytest ai_mesh_gateway/tests/test_enforcement.py` — run below.
- `pytest tests/golden` — run below.
- **Blockers:** docker stack not probed; main.py claim free but edit deferred to iter 2.
