# Cursor Ralph — Pipeline Consolidate + Fix + FREEZE. ONE item/iteration. Never fake green. Log to 4 memories.

## C0 — Coordination
- [ ] 00. Join hive; claim main.py:3500-6600 + policy/scanner/enforcement/output_guard modules + pipeline-trace UI. Read the changelogs. Rebase from main.

## P1 — Map & collapse the two pipelines (root of the leak)
- [x] 01. Enumerate EVERY chat code path: proxy_chat (4255), streaming finalizer (2453), degraded sync path (932), any preview/simulator/pipeline_live path. Diagram each stage sequence → docs/pipeline/PATHS.md. DONE: 4 sub-paths (A standalone-stream, B standalone-sync, C connected-stream, D connected-sync) + F firewall-disabled + R responses-delegate. 6 fail-open sites documented. PIPELINE-0001.
- [x] 02. Identify divergences: where does one path short-circuit on block and another continue? Where does "degraded" fail OPEN? Where do streaming vs non-streaming enforce differently? DONE: docs/pipeline/DIVERGENCES.md — 8 divergences (D-01 to D-19). Highest risk: D-05 (output guard fail-open exception Path B), D-06 (streaming skips reasoning_content/tool_calls). PIPELINE-0002.
- [x] 03. Define ONE canonical pipeline with ONE enforcement authority (a single resolve+enforce function all paths call). Document in docs/pipeline/CANONICAL.md. DONE: 7-stage pipeline (pre→policy→input_scan→enforcement→routing→model→output_guard→finalize). Two entry points: resolve_and_enforce() + enforce_output() → frozen PipelineDecision. Fail-closed contract. Fixes D-05/D-06/D-18. Migration map. Mermaid diagram. PIPELINE-0003.
- [x] 04. Refactor all paths to the canonical enforcement (minimal main.py edits; put logic in enforcement.py). Streaming + non-streaming + degraded all go through it. DONE: PipelineDecision + resolve_and_enforce() + enforce_output() in enforcement.py; input block wired; D-05 fixed (fail-CLOSED). 1824 passed. PIPELINE-0004.

## P2 — Fix the LEAK: block short-circuits, security fails CLOSED
- [x] 05. A block at ANY stage short-circuits: NO downstream stage runs, the model is NEVER called. (Fix L1.) DONE: 6 input-side block returns (threat_intel L5324, keyword L5983, backend_scan L6106, policy L6220, scanner L6603, unmaskable_PII L6745) all precede ALL model calls (L6802/6819 standalone, L7542/7560 connected). Structural source proof + pipeline_trace model-skip verification + enforcement authority terminal-block tests. 24 new tests. PIPELINE-0005.
- [x] 06. Security fails CLOSED: a degraded/unavailable scanner must NOT fail-open raw PII to the model — it redacts or blocks. (No raw PII past input, ever.) DONE: enforcement.py gains tier1_pii_detected; main.py degraded path runs detect_pii/detect_secrets/detect_credential_exposure; redact trigger extended for degraded+PII. Output enforce_output(scan_degraded) already correct. Closes D-02. 19 tests + 1870 suite passed. PIPELINE-0006.
- [x] 07. ONE authoritative final_action + blocked_by; no double-block ambiguity. (Fix L2.) DONE: _build_safe_block_response single-writer (no double-resolution); removed redundant pipeline_stage from 403 JSON. Trace final_action sourced from PipelineDecision.action (not ad-hoc zeroshield/hardcoded). Streaming input_action param. 20 tests + 1894 full suite. PIPELINE-0007.
- [x] 08. Verify: the PII record NEVER reaches the model; a block trace has empty/absent model_output (no 7710ms call). DONE: 26 tests (block→skip, redact→byte-clean, degraded→redact/block, original-leak-signature impossible) + LIVE verification (SSN+email+key→403, model_input/output=skip, 9.7ms). PIPELINE-0008.

## P3 — Correct enforcement: redact, not block (per the org contract)
- [x] 09. Policy matches PII/PCI/PHI and REDACTS before input_scan (fix B-POL: compile+push to POLICY_SYNC, verify regex). (Fix L4.) DONE: ROOT CAUSE = when both redact AND block rules co-match (e.g. PCI PAN redact + CVV block), policy_engine.evaluate returns action="block" (max precedence) and _policy_check_cached only applied redaction_hints when action=="redact", silently discarding them → hard block without redaction. FIX: _policy_check_cached now applies redaction FIRST when action="block"+hints, re-evaluates block rules on masked text; if no block rule still matches → downgrades to "redact" + returns masked prompt for input_scan. Compile+push path verified correct (compiler emits redaction_config, POLICY_SYNC delivers bundles). 18 new tests in test_pipeline_policy_redact.py. Gate: 18 targeted + 1948 full suite passed. PIPELINE-0009.
- [x] 10. resolve_enforcement: REDACT recommendation → REDACT; escalate to block only if org policy=block or redaction byte-impossible. PII record → redact + forward masked. (Fix L5.) DONE: resolve_enforcement monitor-posture downgrade now preserves REDACT (not monitor) when scanner rec=redact and redaction IS possible; unmaskable PII (redaction_possible=False) fail-closed BLOCK overrides monitor posture. Two bugs fixed: (1) policy=block+mode=monitor gave "monitor" (lost PII masking at resolve_enforcement level, compensated by wrapper but contract-violating); (2) unmaskable PII with policy=block under non-block mode was downgraded to "monitor" (honesty check missed is_terminal_block). 17 new tests. Gate: 28 targeted + 1965 full suite passed. PIPELINE-0010.
- [ ] 11. Fix the Tier-1 false positive: plain-text PII must NOT be classified as "Markdown/HTML-obfuscated"; obfuscation detection only fires on actual obfuscation. (Fix L3.)
- [ ] 12. Verify: PII record → policy redact → input_scan sees masked → final_action=redact → masked prompt to model.

## P4 — Fix the output guard
- [ ] 13. Output guard evaluates the ACTUAL model output, never the input echo; an empty output does not "contain PII". (Fix L6, L7.)
- [ ] 14. Redaction (not block) where output carries PII and can be masked; byte-verified (bytes changed). Verify on a real completion.

## P5 — Latency: correct, coherent, reducible
- [ ] 15. Instrument per-stage latency accurately; total_latency == sum(stages) + overhead, reconciled. (Fix L8; use Context7 for FastAPI/ASGI timing best practices.)
- [ ] 16. Frontend Duration/total == backend (fix the "0ms" vs 13555ms); add time-to-first-token for streaming.
- [ ] 17. Add a latency BREAKDOWN + "how to reduce" hints (e.g. model_output 7710ms dominates → smaller/faster model, caching, parallel scans).
- [ ] 18. Verify latency numbers match end-to-end across backend JSON and the UI.

## P6 — Full frontend transparency (the pipeline log/trace)
- [ ] 19. Record + expose the FULL per-stage trace for EVERY event type — including input_blocked/output_blocked (fix L9: "no per-stage trace recorded").
- [ ] 20. For each stage show: action, WHY (matched policies/rules, guard reason, tier, confidence), exact latency, prompt_in/prompt_out (redacted-safe), and the decision source.
- [ ] 21. Show the ROUTING decision explicitly: where the request went — RAG / Vector DB / MCP / which model — and why (the adjudicator factors/weights). (Answers "where to route".)
- [ ] 22. Show the actual INPUT and OUTPUT (redacted-safe) so operators see what was blocked/redacted and why.
- [ ] 23. Fix event conflation: one report = one request_id; no mixing of different prompts/IDs. (Fix L10.)
- [ ] 24. Impeccable-revamp the pipeline-trace view: full-transparency, aligned, responsive (1440/1024/768/375), both themes, detector clean.
- [ ] 25. Verify (Browser MCP) the full history renders for blocked AND redacted AND allowed events, with correct latency + routing + reasons.

## P7 — 100-policy CISO/industry-standard package
- [ ] 26. Design a 100-rule package across: OWASP LLM Top-10 (injection, sensitive-info disclosure, supply-chain, data poisoning, improper output, excessive agency, system-prompt leakage, vector/embedding weaknesses, misinformation, unbounded consumption); PII (GDPR), PCI-DSS, PHI (HIPAA); secrets/credentials; code/IP exfil; jailbreak/guardrail bypass; toxicity/hate/NSFW/self-harm/violence; unauthorized advice; brand/off-topic; plus sector packs (finance, healthcare, legal). Map each to action (block/redact/monitor) + severity + priority. → docs/policies/CISO_100.md.
- [ ] 27. Implement as a seed package (seed_policy_package) with correct regex/keyword/semantic rules; compile + push to POLICY_SYNC.
- [ ] 28. Verify each policy matches its positive fixtures and does NOT false-positive on negatives (incl. the L3 plain-PII case).

## P8 — FREEZE (golden characterization suite)
- [ ] 29. Extend tests/golden/ to pin the CANONICAL pipeline: every stage's action + short-circuit behavior + latency-sum invariant + routing target, per use case (PII redact, PHI redact, jailbreak block, injection block, secrets block, benign allow, kill-switch reroute, degraded-scanner FAIL-CLOSED, output-guard redact). Snapshot normalized stages[].
- [ ] 30. A GOLDEN CASE for L1: input-blocked request → model_output absent (model NOT called) → no PII egress (byte-verified).
- [ ] 31. A GOLDEN CASE for the trace UI: Playwright DOM snapshot proving the full per-stage history renders for blocked/redacted/allowed events with correct latency + routing + reasons.
- [ ] 32. Wire the CI gate: the pipeline golden snapshot can't change without a deliberate re-bless — protects it from the parallel Claude sessions' main.py edits.

## P9 — Verify + freeze
- [ ] 33. Re-run P2-P8 3× (in-process + live). The PII record redacts (never blocks-then-sends); blocks short-circuit; latency coherent; full transparency renders; 100 policies active; degraded path fails closed. All green 3× → <promise>COMPLETE</promise>.