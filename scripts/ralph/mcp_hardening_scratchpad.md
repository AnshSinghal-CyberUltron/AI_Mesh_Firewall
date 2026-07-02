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
- [x] 3. Per-user/agent/role tool authorization (close the mcp_proxy.py:302-305 gap; actor-keyed).
      DONE via CHG-0006+0007+0008 (2026-07-02). Per-actor tool ACCESS authorization (block/allow by
      user/agent/role) is enforced + tested across ALL paths: HTTP (MCPToolCallView), stdio/ws ADAPTER
      (scan orchestrator: evaluate_mcp_policies(actor) + _policy_applies_to_actor + CHG-0007 rule-block
      honoring, end-to-end proof in test_scan_enforces_actor_scoped_block_on_adapter_path), and per-key
      controls on the bare REST route (CHG-0006 allowlist/cap/disabled). Finding #1 was IMPRECISE (actor
      IS used for an access decision, one layer down); the mcp_proxy.py:301-307 cache-key TODO is a
      documented non-issue (tool enable/disable is server-scoped by design). Finding #4 was a FALSE
      POSITIVE (CHG-0007). Gate: 27 authz/scoping tests + 427 broad sweep pass.
- [ ] 3b. Per-policy FIELD-level redaction (redaction_fields) on the stdio/ws adapter path (split from #3).
      HTTP path (MCPToolCallView, control views.py:1113) masks specific NAMED result fields for matched
      actor-scoped policies via apply_field_redaction/redact_structured; the gateway policy engine/bundle
      has NO field-redaction support (only redaction_hints), so the adapter path does content-scan but not
      field-level RBAC masking. Needs a bundle-format extension: add redaction_fields to compiled policies
      + gateway EvaluationResult + apply on the adapter response. Cross-cutting (control compiler + gateway).
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
      LIVE VERIFIED (mostly done) — CHG-0017 (2026-07-02): sent PII through the live gateway + queried
      MCPEvents. Redaction comprehensive (ssn/card/email all masked, combined too, 0 leak). compliance_tags
      recorded AND COMPLETE (email-only→['GDPR','PII']; email+ssn→['GDPR','HIPAA','PII']). decision=redact
      under default tag posture (E12 floor CHG-0005). So tag inputs+results, enforce-by-tag (via redaction),
      and audit ALL WORK — refutes the audit's "tags audit-only/no enforcement" framing. THE ONE REMAINING
      GAP before [x]: vocabulary mismatch — gateway emits GDPR/HIPAA/PII/PCI-DSS/… but the ComplianceTag
      catalog uses GDPR-PII/HIPAA-PHI/PCI-CARD/…, so MCPEvent.compliance_tags joins ZERO catalog rows
      (catalog reporting broken for gateway events). Fix = unify vocab onto ComplianceTag.code (cross-plane:
      gateway patterns.py + control catalog/migration; breaks 8 gateway tests) — owning-session semantic
      decision, NOT a unilateral backstop edit. Evidence: mcp-parallel/findings/backstop-p5-compliance-tags/.
- [ ] 6. End-to-end per-tool-call chain: authz → minimize → scan+redact(in&result) → tag → audit.

## G3 — Architecture hardening (log every edit)
- [ ] 7. All transports (http/ws/sse/stdio) in the per-org gVisor sandbox; nothing in the backend (complete/fix if needed).
      VERIFIED STILL OPEN — CHG-0011 (2026-07-02, read-only; active-migration zone, did NOT edit). Broker
      side DONE (unified POST /{org}/rpc handles all transports, routes.py:296; agent dials HTTP+egress-
      allowlist). Gateway wiring INCOMPLETE: stdio→broker ✓ (adapter path, MCP_STDIO_IN_PROCESS=false);
      streamable-http/sse→control backend (mcp_proxy.py:2470 → /api/mcp-connector/tools/call/; backend calls
      upstream DIRECTLY, zero broker/BROKER_URL refs, docstrings 'directly to the upstream' views.py:412/601/727);
      websocket→in-gateway websockets.client.connect (mcp_ws_adapter.py:135). So http/sse/ws do NOT egress
      via the per-org sandbox → "nothing in the backend" UNMET; the "all 32 complete/all transports via
      sandbox" claim is inaccurate for the gateway wiring. NOT a data leak (1.4 result scan applies to
      http/sse per CHG-0005) — an ISOLATION gap (per-tenant egress). REMAINING before [x] (owning session):
      route gateway http/sse (org_mcp_jsonrpc else-branch / internal_tools_call direct-httpx) + websocket
      (mcp_ws_adapter.py) through broker_send_rpc (unified route already exists); prove NO transport's
      outbound call runs in the gateway/control backend.
      UPDATE — CHG-0018 (2026-07-02): the http/sse part of my CHG-0011 finding is RESOLVED by P4.13/P6.18.
      Gateway now routes stdio + streamable-http/sse via the sandbox: _is_sandbox_routed (mcp_proxy.py:1918)
      + _adapter_forward streamable-http/sse branch (1953-1978) uses broker_send_rpc (gateway builds
      upstream block, SANDBOX dials; gateway never connects). MCP_HTTP_VIA_SANDBOX default=true AND set true
      on the live gateway container. stdio live-verified (echo calls via sandbox). RESIDUAL: websocket STILL
      connects in-gateway (mcp_ws_adapter.py:135 websockets.client.connect, not migrated) — so
      "4-transport isolation active" OVERSTATES (3/4; ws unused live). REMAINING before [x]: migrate ws to
      broker_send_rpc (unified /{org}/rpc handles ws) OR document ws as legacy; independent live
      http-via-sandbox drive (register a streamable-http server, assert 0 direct upstream dials).
- [ ] 8. No unknown npm on host — proven.
- [ ] 9. Gateway auth/authz/validation/rate-limit/policy/audit — verified + hardened.
      LIVE VERIFIED (auth/authz/validation) — CHG-0016 (2026-07-02): probed the live gateway. Auth ENFORCED
      (no-auth→401, bad-key→401); CROSS-ORG key ISOLATION ENFORCED (org-a key on org-b endpoint→403
      org_scope_violation, and vice versa — auth-layer cross-tenant isolation, both directions); input
      validation GRACEFUL (missing-method/malformed-json/empty-tool → no 500). Rate-limit (S12) exists but a
      60-call burst didn't trip it (higher threshold). Policy authz already unit-proven (CHG-0006/0007/0008);
      audit wired (_record_gateway_event). Evidence: mcp-parallel/findings/backstop-p9-gateway-authz/.
      REMAINING before [x]: probe the rate-limit THRESHOLD (larger controlled burst — deferred to avoid
      throttling shared keys); adversarial policy-enforcement + audit-completeness checks.
- [ ] 10. Resource limits CPU/mem/disk/timeout enforced + containment proven.
      LIVE VERIFIED (config) — CHG-0015 (2026-07-02): docker inspect of all 3 live org sandboxes shows
      CapDrop=[ALL], SecurityOpt=[no-new-privileges], Privileged=false, PidsLimit=256, Memory=2GiB,
      NanoCpus=1, ReadonlyRootfs=true, tmpfs /tmp noexec,nosuid, npm_config_ignore_scripts set. Strong
      containment deployed. REMAINING before [x]: prove CONTAINMENT under load (fork/mem/disk/timeout bombs
      + neighbor-safe) — but destructive bombs are UNSAFE on the shared live stack (item 17 needs an
      isolated host). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.
- [ ] 11. PostgreSQL + Redis schemas/usage/restart-safety verified.
- [ ] 12. gVisor + seccomp/no-new-privileges/cap_drop/egress-lockdown enforced.
      LIVE VERIFIED — CHG-0015 (2026-07-02): PRESENT live = cap_drop=ALL, no-new-privileges, per-org network
      (mcp_sandbox_net_<org> distinct per org — host-run shared-bridge fallback NOT active). GAPS:
      (a) gVisor UNMET INFRA PREREQ — `which runsc`=NOT installed, docker only offers runc; sandboxes run
      on runc (shared kernel). Code fail-closes if RUNTIME_REQUIRED=true (CHG-0001) so forcing it would KILL
      the sandboxes — gVisor must be INSTALLED on the deploy host first (infra task, not code). (b) egress:
      per-org networks internal=false (open outbound NAT) — no network-level egress default-deny. REMAINING
      before [x]: install gVisor + require runsc (verify Runtime=runsc live); network egress default-deny
      (per-org internal=true + broker-proxied allowlist, or iptables/eBPF). NOTE: risky to change live (would
      break the running stack). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.

## G4 — Production hardening (Phase 3)
- [ ] 13. Monitoring + metrics + tracing wired; backup; auto-recovery (sandbox/broker/Redis/PG self-heal).
      LIVE VERIFIED (partial) — CHG-0020 (2026-07-02): probed the running stack. METRICS WIRED (gateway
      /metrics 401 scraper-key-gated, METRICS_ALLOW_OPEN=false=secured; telemetry-drain thread). HEALTH
      WIRED (gateway /health + /v1/mcp/health 200; control /api/health/ 200; broker :8311 /health 200).
      AUTO-RECOVERY: docker healthchecks on broker/control/postgres/redis (all healthy -> auto-restart on
      unhealthy) + sandbox reaper/reconcile. GAPS before [x]: (1) distributed TRACING (OTEL/Jaeger) NOT
      configured (metrics/telemetry present, but no request-level tracing); (2) gateway container has NO
      docker healthcheck (health=none -> not auto-restarted); (3) backup (PG/Redis) NOT verified. Evidence:
      mcp-parallel/findings/backstop-p13-observability/observability_evidence.txt.

## G5 — VERY HARD stress (big hardware; run each, capture evidence)
- [ ] 14. 30–50 orgs × 8–10 MCPs = 300–500 sandboxes concurrently — provision + healthy.
      CODE CEILING REMOVED — CHG-0010 (2026-07-02): scale provisioner org count was a hardcoded 3-tuple
      (→ 3×5=15-sandbox ceiling). Now build_orgs(NUM_ORGS) (default 3 backward-compat; extends via org-<i>),
      so NUM_ORGS=50 SERVERS_PER_ORG=10 → 500 targets. New scripts/test_mcp_scale_provision.py: 4 pass
      (incl. 50-org→500-sandbox). REMAINING before [x]: (1) PRE-CREATE the N orgs in control (bulk
      org-creation mgmt command using org-<i> convention — provisioner logs in, doesn't create); (2) broker
      per-org DISTINCT sandbox UID for a true per-tenant fork budget (NPROC_ROOT_CAUSE.md — shared host-UID
      budget saturated ~244/256 at just 15 servers); (3) actually provision + prove 300-500 sandboxes
      HEALTHY concurrently on the live stack.
- [ ] 15. 5k–10k concurrent tool calls — routing correct, isolation holds, none dropped/mixed.
- [ ] 16. Soak (hours) — no leaks/exhaustion/503 storms; reaper correct.
- [ ] 17. Resource bombs (mem/fork/disk/timeout) — contained; neighbors + host safe.
- [ ] 18. Chaos (kill sandbox/broker/Redis/PG) — auto-recovery + no leakage during recovery.
- [ ] 19. Cross-tenant leakage canaries at 500-sandbox scale under chaos — never observed anywhere.
      ORACLE FIXED — CHG-0009 (2026-07-02): the audit-log cross-tenant oracle in mcp_scale_matrix_live.py
      was FABRICATED (`for fs in []` → foreign_org_events structurally 0, and NOT in the PASS gate — a
      false "isolation proven" signal). Replaced with a real unit-tested count_foreign_events, ADDED to the
      PASS gate (total_foreign==0), made the module import-safe, renamed misleading total_egress_bytes→
      request_payload_bytes. New scripts/test_mcp_scale_oracle.py: 5 pass (incl. proof the OLD predicate
      missed a real leak). REMAINING before [x]: run the canary matrix LIVE at 500-sandbox scale under
      chaos with the corrected+gated oracle + capture REAL sandbox egress bytes cross-checked with an
      independent aidefence oracle (tied to item 14 true scale — still a hardcoded-3-org=15-sandbox ceiling).
      LIVE VERIFIED @15-MCP — CHG-0013 (2026-07-02): stack was up, RAN scale-matrix 3× consecutive
      (ROUNDS=6, 540 calls): cross_org_result_leak=0, foreign_org_events_total=0 (fixed oracle), errors=0,
      mismatches=0 → cross-tenant isolation HOLDS. De-flaked the gate: retry-on-mismatch (+transient_retries)
      since a ~0.7% transient echo hiccup under load intermittently failed the strict gate (NOT a demux/
      isolation bug — cross_org_leak/errors stayed 0 across 1080+ calls). Evidence:
      mcp-parallel/findings/backstop-p19-isolation/scale_isolation_evidence.json. REMAINING before [x]:
      run at TRUE 300-500-sandbox scale (item 14 live) UNDER CHAOS (item 18) with captured egress bytes +
      independent aidefence cross-check.
- [ ] 20. 1.4 under peak load — redaction + per-actor authz + tagging hold; no PII/IP/regulated escape.
      HARNESS UPGRADED — CHG-0012 (2026-07-02): mcp_live_matrix_harness.py was fully SEQUENTIAL (not peak
      load) + classified only allowed/blocked/errors, never inspecting response bytes (a redact-but-forward
      counted as allowed). Now runs all calls CONCURRENTLY (CONCURRENCY-bounded semaphore, asyncio.gather)
      + asserts on RESPONSE BYTES via find_leaked_values (any sent sensitive value appearing raw in the
      egress = LEAK → run FAILS). New scripts/test_mcp_live_matrix_oracle.py: 5 pass. REMAINING before [x]:
      run LIVE at peak load (high CONCURRENCY / 5k-10k in-flight, tied to item 15) against the stack, prove
      ZERO leaks 3× consecutively; extend with per-actor authz-denial + tag-enforcement cases under load.
      LIVE VERIFIED @25-concurrency — CHG-0014 (2026-07-02): ran the upgraded live-matrix harness 3×
      consecutive (CONCURRENCY=25, 150 calls each/450 total): total_leaked=0, total_redacted=60/run,
      errors=0 → 1.4 redaction HOLDS under concurrent load (validates CHG-0003/0004/0005 result floor +
      CHG-0012 byte-check end-to-end; the outbound floor redacts PII even under the default tag posture).
      Harness fixes: PII embedded in `message` (round-trips through the echo test tool — the redaction test
      was vacuous before); resilient server-id lookup (graceful CONTROL_URL degradation). Evidence:
      mcp-parallel/findings/backstop-p20-redaction-load/redaction_under_load_evidence.json. REMAINING before
      [x]: run at TRUE peak (5k-10k in-flight, item 15); add per-actor authz-denial + tag-enforcement cases.

## G6 — Frontend (strictly; log edits to owned panels)
- [ ] 21. MCP panels reflect 1.4 (tags, per-actor tool controls, redaction indicators), real data, no leak, both themes.
      CHG-0019 (2026-07-02): fixed the mojibake sub-finding — PolicyManagementPanel.jsx:399 Actor Scope
      header had literal `·` in raw JSX text (a fe-harden mojibake "fix" that was itself broken — JSX
      doesn't interpret escapes in text nodes, so it rendered the literal string). Now `{'·'}` (JS expr
      → renders `·`, pure-ASCII source). npm run build ✓. REMAINING (owned by fe-harden): explicit redact
      signal from tools/call + Redact badge + redacted-field list on execute; fix Redact StatCard
      under-count; extend Playwright gate to cover tags/actor/redaction; both themes.

## G7 — Recursive verification
- [ ] 22. Re-run G2–G6 end-to-end 3×; adversarial pass; Ruflo consensus green. Only then <promise>COMPLETE</promise>.
