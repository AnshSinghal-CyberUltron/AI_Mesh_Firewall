# Claude Code Ralph — MCP Hardening BACKSTOP (edits-with-memory-log; 50–100 iters)
# RULE: every change → log to Ruflo + AGENTS.md + .cursor/rules + docs/mcp/HARDENING_CHANGELOG.md (§0).

## G0 — Setup + memory protocol
- [x] 0. Init the four-memory changelog; join hive; enforce MCP_SANDBOX_RUNTIME=runsc.
      CHG-0001 (2026-07-02): created docs/mcp/HARDENING_CHANGELOG.md (§0 protocol + template),
      .cursor/rules/mcp-hardening-changelog.mdc, AGENTS.md pointer. Audited runsc fail-closed
      enforcement in docker_manager._resolve_runtime() (docker_manager.py:343-359) — mechanism
      present & correct; prod must set MCP_SANDBOX_RUNTIME=runsc + MCP_SANDBOX_RUNTIME_REQUIRED=true
      (env-set verification tracked under G3 item 12).

## G1 — Backstop audit (what did the other sessions do/miss?)
- [x] 1. Review the cursor-mcp / stress / frontend branches + shared memory; list mistakes, omissions,
      regressions, and incomplete work → docs/mcp/BACKSTOP_FINDINGS.md.
      CHG-0002 (2026-07-02): 6-auditor parallel workflow → docs/mcp/BACKSTOP_FINDINGS.md (24 findings:
      13 high/8 med/1 low). Signal = OMISSIONS/fail-open parity gaps, not cross-session regressions.
      3 load-bearing claims backstop-verified (SSE unscanned egress; dead cross-tenant oracle
      `for fs in []`; 3-org=15-sandbox ceiling). PRIORITY ORDER for next items:
        #1 G2 item 2 — fail-closed byte-verified RESULT redaction (SSE /ext-proxy + string/structuredContent
           shapes + fail-OPEN result-floor all egress raw PII/secret TODAY — the most direct leak).
        #2 G2 item 3 — per-actor authz + field-redaction on stdio/ws adapter path (+posture-vs-rule block
           downgrade, +allowlist-scope inversion, +org_mcp_tool_call bypasses allowlist/cap).
        #3 G2 item 5 — unify tag vocab onto ComplianceTag.code + tag→action enforcement + tag input-blocks.
        #4 G3 item 12 — runsc REQUIRED + network-level egress default-deny (shipped default = runc+open NAT).
        #5 G3 item 7 — broker RPC for http/sse/ws (only stdio sandboxed) + fix host-run shared-bridge fallback.
        #6 G5 item 19 — replace dead cross-tenant oracle + capture real egress bytes + aidefence cross-check.
        #7 G5 14/15/18/16/17/20 — build true-scale stress (300-500 sandboxes, 5k-10k calls, chaos/soak/bomb/peak).
        #8 G6 item 21 — emit redact signal, Redact badge+field list, fix StatCard under-count, extend Playwright, fix mojibake.

## G2 — 1.4 Context Assembly & MCP Guardrails (log every edit)
- [x] 2. Field-level redaction of MCP tool RESULTS (byte-verified, fail-closed).
      DONE via CHG-0003+0004+0005 (2026-07-02). Result redaction is byte + independent-aidefence-oracle
      verified across ALL bare routes (rest/internal/ext streaming+non-streaming) and ALL result shapes
      (content/structuredContent/list/str); fail-CLOSED on the bare routes, fail-SAFE (500, no raw egress)
      on the main org_mcp_jsonrpc path (audited: scan exception propagates → 500, raw returned only after
      a successful scan). Gate: test_mcp_bare_proxy_scan.py 14 passed; broad sweep 362 passed.
      DEFERRED (NOT leaks): main-path graceful-block vs 500 (availability enhancement); per-actor
      FIELD-level RBAC masking → tracked under item 3.
      History — CHG-0003 (fail-closed result-scan error). `_scan_tool_result_floor`
      (mcp_proxy.py ~690-780) now blocks (SCAN_ERROR + result_scan_failclosed) instead of forwarding RAW
      on a scan exception — BOTH the primary output scan AND the redaction-floor re-scan. +2 byte-level
      tests (scanner patched to raise → raw PII absent + blocked); test_mcp_bare_proxy_scan.py 10 passed,
      broad sweep 323 passed.
      CHG-0004 (2026-07-02): (a) SSE buffer-and-scan DONE — ext_mcp_proxy buffers finite tools/call SSE,
      scans/redacts each data-frame result (_scan_reframe_sse_tool_result), re-emits masked or blocks;
      non-tools/call SSE passes through (no hang). 3 SSE tests replace the leak-pinning test;
      test_mcp_bare_proxy_scan.py 12 passed, broad sweep 342 passed; independent aidefence oracle:
      masked egress hasPII=false, raw hasPII=true.
      REMAINING before [x]: (b) non-streaming string/structuredContent result shapes unscanned;
      (c) audit main org_mcp_jsonrpc inline result path (~2265/2451) for the same fail-open.
- [ ] 3. Per-user/agent/role tool authorization (close the mcp_proxy.py:302-305 gap; actor-keyed).
      PARTIAL — CHG-0006 (2026-07-02): finding #2 closed. org_mcp_tool_call (bare REST route) now enforces
      the three per-key gates it lacked — _tool_allowed_by_key (403), mcp_max_tool_calls cap (429),
      _is_tool_disabled (403) — before forwarding, at parity with org_mcp_jsonrpc. +4 tests; 18 bare-proxy
      passed, broad sweep 424 passed. REMAINING before [x]: (#1, the big one) per-actor authz + field-RBAC
      masking NOT enforced on stdio/ws ADAPTER path (actor threaded for scan attribution only; enabled-tools
      payload has no actor dimension); (#3) Tier-1/2 policy BLOCK gated on posture not the rule's action
      (actor-scoped block downgraded to tag under default posture, mcp_scan_orchestrator.py); (#4)
      _policy_applies_to_actor allowlist-scope inverts intent (policy_engine.py).
      CHG-0007 (2026-07-02): #3 FIXED — Tier-1 policy scan (_scan_text_tier1) now honors a matched rule's
      own action='block' under any non-monitor posture (was downgraded to tag under default posture; the
      stdio/ws adapter path bypasses the backend that re-enforces the rule — now at parity with the
      control-plane engine). +2 tests; orchestrator 15 passed, broad sweep 427 passed.
      #4 DISMISSED as FALSE POSITIVE — _policy_applies_to_actor is a policy-SCOPING primitive (allowed_* =
      actors the policy APPLIES to; documented + mirrors control-plane engine); "block scoped to admins"
      is coherent scoping, NOT an inversion. Inverting would break the contract + control-plane parity +
      existing policies. Do NOT touch it. The auditor conflated scoping with the ABSENT deny-by-default
      per-actor tool-authz primitive (= finding #1).
      REMAINING before [x]: finding #1 (the big one) — per-actor user/agent/role tool authz + field-RBAC
      masking on the stdio/ws ADAPTER path (enabled-tools payload needs an actor dimension, or route the
      adapter path through actor-scoped policy eval).
- [ ] 4. Context minimization / least-privilege assembly.
- [ ] 5. Compliance tagging: extend mcp_compliance_tags.py to PII/IP/regulated; tag inputs + results; enforce by tag; audit.
- [ ] 6. End-to-end per-tool-call chain: authz → minimize → scan+redact(in&result) → tag → audit.

## G3 — Architecture hardening (log every edit)
- [ ] 7. All transports (http/ws/sse/stdio) in the per-org gVisor sandbox; nothing in the backend (complete/fix if needed).
- [ ] 8. No unknown npm on host — proven.
- [ ] 9. Gateway auth/authz/validation/rate-limit/policy/audit — verified + hardened.
- [ ] 10. Resource limits CPU/mem/disk/timeout enforced + containment proven.
- [ ] 11. PostgreSQL + Redis schemas/usage/restart-safety verified.
- [ ] 12. gVisor + seccomp/no-new-privileges/cap_drop/egress-lockdown enforced.

## G4 — Production hardening (Phase 3)
- [ ] 13. Monitoring + metrics + tracing wired; backup; auto-recovery (sandbox/broker/Redis/PG self-heal).

## G5 — VERY HARD stress (big hardware; run each, capture evidence)
- [ ] 14. 30–50 orgs × 8–10 MCPs = 300–500 sandboxes concurrently — provision + healthy.
- [ ] 15. 5k–10k concurrent tool calls — routing correct, isolation holds, none dropped/mixed.
- [ ] 16. Soak (hours) — no leaks/exhaustion/503 storms; reaper correct.
- [ ] 17. Resource bombs (mem/fork/disk/timeout) — contained; neighbors + host safe.
- [ ] 18. Chaos (kill sandbox/broker/Redis/PG) — auto-recovery + no leakage during recovery.
- [ ] 19. Cross-tenant leakage canaries at 500-sandbox scale under chaos — never observed anywhere.
- [ ] 20. 1.4 under peak load — redaction + per-actor authz + tagging hold; no PII/IP/regulated escape.

## G6 — Frontend (strictly; log edits to owned panels)
- [ ] 21. MCP panels reflect 1.4 (tags, per-actor tool controls, redaction indicators), real data, no leak, both themes.

## G7 — Recursive verification
- [ ] 22. Re-run G2–G6 end-to-end 3×; adversarial pass; Ruflo consensus green. Only then <promise>COMPLETE</promise>.
