# MCP Platform Validation — Root-Cause & Production-Readiness Report

Scope: end-to-end validation of the MCP firewall (frontend config → control plane → gateway →
broker → sandbox → tool execution → scanning → response), driven against the LIVE docker stack
(gateway:8300, control:8100, broker:8311, postgres, 5 seeded orgs, connected stdio/http/sse/ws MCP
servers). Evidence = code refs + live requests/responses + DB events + reproducible probes.

Method note: several fixes below live in the working tree but are **uncommitted**, because
`gateway/ai_mesh_gateway/mcp_proxy.py` and `mcp_scan_orchestrator.py` are being concurrently edited by
other sessions (shared worktree). Each fix is verified in isolation (unit tests + HEAD-worktree
baselines proving no self-caused regressions). They reach the running container on the next image
rebuild (the deploy path here rebuilds from the working tree).

---

## 1. Root-cause report (issues found)

| # | Issue | Severity | Root cause | Status |
|---|-------|----------|-----------|--------|
| 1 | Scanning ran with 0 scan controls (product decision: it must NOT) | High (product) | `resolve_effective_controls` reported `scan_controls_configured=True` always; gateway had no skip gate | **FIXED & LIVE-VERIFIED** — gate in `_mcp_security_scan` on `scan_controls_configured is False` (fails safe when flag absent). Live: 0 controls → `decision=scan_skipped`, SSN egresses raw, no tags. |
| 8 | Field-redaction bypass for ≥2-level-nested JSON in RAG grounding | High (data leak) | `_JSON_BLOCK_RE` matched only ≤1-deep JSON; top-level classified fields beside a deep sibling escaped `_redact_dict` | **FIXED & COMMITTED** (`c3d412f5`) — brace-balanced string-aware span scanner. 10 regression tests. |
| 15 | Disabling a server did not block access (`is_active`/`is_exposed_to_agents` unenforced) | Med-High (access control) | Flags stored + version-bumped but never consumed for gating; only per-*tool* disable was enforced | **FIXED (deployed via rebuild) & LIVE-VERIFIED; git-uncommitted** — `_server_disabled` gate on all 6 gateway surfaces (JSON-RPC, 3 REST, 2 internal). 10 tests. Live: disable → tool call BLOCKED. |
| 17 | Server-disable was eventually-consistent (≤120s) | Med (window of exposure) | `_get_server_config` was TTL-only cached (120s), not version-invalidated, so `_server_disabled` read stale `is_active=True` | **FIXED (deployed) & LIVE-VERIFIED; git-uncommitted** — version-invalidate `_get_server_config` via `_current_scan_version`. Live: warm-cache disable now BLOCKS at T+2s (was ≤120s lag). 2 tests. |
| 18 | MCP Tier-2 silently never ran (Bedrock scan skipped even when enabled) | Med-High (false security) | `_get_input_scanner()` used bare `import main` → resolved a module whose startup-set `INPUT_SCANNER` was None → `scan_prompt_with_tier2` returned `scanner_unavailable` (fail-open) | **FIXED (deployed) & LIVE-VERIFIED; git-uncommitted** — resolve via `sys.modules`. Live: enabling Tier-2 now shows real `tier2` scan stages (Bedrock reached), no `scanner_unavailable`. 3 tests. |
| 19 | Same bare-`import main` class systemic: MCP per-org rate limiting bypassed | Med-High (DoS/abuse) | `_mcp_org_rate_limit_raw` → `_enforce_org_tpm_rate_limit(rate_limiter=RATE_LIMITER)`; RATE_LIMITER None on the bare module → `enforce_org_tpm_rate_limit` no-ops. Also 2 CONFIG-flag helpers (low, fail-secure) | **FIXED (deployed) & LIVE-VERIFIED; git-uncommitted** — shared `_gateway_app_module()` resolver. Live: Redis now shows `ratelimit:*:burst:*`/`:tpm:*` counters (limiter executes; was bypassed, wrote nothing). 2 tests. |
| 20 | `IntegratedSecurityScanner.scan_complete(mcp_data=)` TypeError → HTTP 500 on every policy-eval scan carrying MCP data | Med-High (control-plane crash) | Callers pass `mcp_data=`; the param + the purpose-built `_derive_agent_data_from_mcp` converter were never wired into `scan_complete` (dead code) | **FIXED & COMMITTED (`36c4c1aa`) & LIVE-VERIFIED** (after control rebuild) — added param + wired the converter. Live: `action=block_immediately` (no crash); Bedrock flags AGENTIC01 goal-hijacking on MCP data. |
| 21 | `scan_conversation` dropped response-side high-severity PII/overall-severity when the prompt had none | Med (missed leak) | Ternary `pii_prompt if response!='critical' else pii_response` biased to prompt; a `high` response-only leak was discarded | **FIXED & COMMITTED (`d30c602b`)** — combine by MAX severity (shared `_SEVERITY_RANK`/`_max_severity`). Verified standalone; live-verify pending control rebuild (control runs baked source). |
| 22 | PHI (critical) detection skipped for `"Medical"`/`"MEDICAL"`/`" medical"` context | Med (compliance miss) | `pii_detector.py:138` case-sensitive `context == "medical"` against a request-body value | **FIXED & COMMITTED (`382d23de`)** — `(context or "").strip().lower() == "medical"`. Verified standalone; live-verify pending control rebuild. |
| 23 | Agentic detectors raised uncaught `AttributeError` → HTTP 500 on a non-dict action entry (e.g. bare tool-name string) | Med-High (control-plane crash) | `detect_agentic01/02/03` call `.get()` on each `current_actions`/`action_history`/`attempted_actions` entry assuming dicts; reachable now that #20 wires MCP tool activity into the agentic path | **FIXED & COMMITTED (`0843fc91`)** — `_coerce_action` normalizes the four action-lists to dicts at the top of `scan()`. Verified standalone (mixed string+dict no longer crashes); live-verify pending control rebuild. Sweep confirmed no sibling assume-type crash remains on the agentic path. |
| 24 | Nondeterministic scan-control **conflict resolution**: two rows equal on `(scope_type, server_id, tool_name, tier, direction, priority)` but conflicting `action`/`enabled` resolve by DB physical order | Med (security nondeterminism; needs misconfig) | `_pick_control` `sort_key=(scope_rank, priority)`; `max()` returns first-of-ties; `Meta.ordering=["-priority","tier","direction"]` gives no discriminator among rows already equal on all three; **no `UniqueConstraint` on the model and no duplicate-guard in `MCPScanControlSerializer.validate`** → duplicates are creatable via the API | **DOCUMENTED + EXECUTABLE-PROVEN; regression test committed (`769701b2`)** — code-traced in the running control container AND reproduced standalone: same two conflicting tool-scope rows resolve `block` vs `monitor` purely by list order. A `block` intended to win can be silently overridden by a tied `monitor`, or `enabled=True` by `enabled=False`, unpredictably. Regression test `test_scan_control_conflict_determinism.py` encodes the desired order-independence invariant (`@expectedFailure` until fixed). Fix sites (`scan_controls.py` sort_key, `serializers.py` duplicate-guard, `models.py` `UniqueConstraint`+migration) are all in co-mingled working-tree files → no clean isolated *code* commit possible now. Recommended fix below. |
| 25 | Org-level **Tier-2 "Enabled" toggle is a silent no-op** without a per-scope tier2 scan-control row — frontend promises the org control "forces" Tier-2 for MCP tool calls; backend never runs it | Med-High (false sense of security — user believes semantic Tier-2/Bedrock scan is active; it is not) | Gateway gate (`mcp_scan_orchestrator.py:965`) `if not tier2_ctrl.get("enabled", False): return` REQUIRES a tier2 matrix row (`DEFAULT_TIER2.enabled=False`); the org flag `mcp_tier2_enabled` is consulted only at `:968` as a kill-switch (`if org_override is False: skip`), NEVER as an independent enabler. Control-plane view (`mcp_connector/views.py:2567-2612`) builds `effective_scan_controls` purely from `resolve_effective_controls(scan_rows)` and returns `mcp_tier2_enabled` as a SIBLING field it never folds into the tier2 control. So org "Enabled" (True) is functionally identical to "Inherit" (None) — only "Disabled" (False) has effect. Frontend (`MCPScanControlMatrix.jsx:217` "off unless enabled by a row OR the org control above"; `:616` "Enabled/Disabled force it for MCP tool calls") presents it as an independent forcer. | **DOCUMENTED + LIVE-CORROBORATED** — full-stack traced (frontend copy → `/api/firewall/config/` PUT sets only the flag, creates no row → control view → gateway gate). Live: `zeroshield` org has `mcp_tier2_enabled=True` + 0 tier2 rows. Bites specifically when an org HAS scan controls (tier1 rows, so scanning is on) + org Tier-2 "Enabled" + no tier2 row (the 0-controls case is separately warned by `MCPConnectorPanel.jsx:1496`). Fix is a product-semantics decision (make org "Enabled" seed the tier2 org-scope default `enabled=True` — either `if tier2_ctrl.enabled OR org_override is True` at the gate, OR fold `mcp_tier2_enabled` into `resolve_effective_controls`'s tier2 default). Both fix sites co-mingled; deferred like #24. |
| 26 | Residual output enforcement at **0 scan controls**: `_scan_tool_result_floor` still hard-blocks (a) resource limits (content-block-count/depth/node) and (b) a **cross-block-split-secret** — outside the off-by-default gate | Low (narrow adversarial trigger; contradicts decision #1 + the code's own "no blocking" comment) | Fix #1's off-by-default gate lives in `_mcp_security_scan` (`mcp_proxy.py:1865`) and correctly no-ops the two-tier scan (live-proven: plain SSN egresses raw at 0 controls). But `_scan_tool_result_floor` (called by `org_mcp_tool_call:5187/5301` with NO upstream `scan_controls_configured` gate) runs its OWN checks first: resource guards (`:1437/1466/1489`) and `_result_has_split_secret` (`:1518`), gated only by `not _explicit_monitor_posture` — which returns False (floors ON) when `scan_controls_configured` is False. So a split secret or oversized result is still blocked at 0 controls. | **DOCUMENTED — verified root cause** (full code trace, org-authenticated path). BALANCED: resource guards are defensible AVAILABILITY floors (not content scanning; removing them exposes a resource-bomb DoS) — recommend KEEP. The split-secret block IS content inspection → the genuine decision-#1 inconsistency; it is a narrow always-on credential-egress backstop (defense-in-depth). Product decision: either (a) exempt always-on safety/availability floors from decision #1 and CORRECT the misleading `:1855-1857` comment, or (b) also gate `_result_has_split_secret` on `scan_controls_configured` for parity with the two-tier skip. Fix site co-mingled; deferred. **Input/output asymmetry confirms the fix:** the INPUT credential force-block (`:1316`/`:4393`) is correctly OFF at 0 controls because it depends on `_findings_have_credential(findings, tags)` from the off-by-default-gated main scan (empty findings → no block); there is NO independent input content-scan. The OUTPUT split-secret block is the lone anomaly because it runs an INDEPENDENT `_result_has_split_secret(result_content)` scan. Reference fix (option b): mirror the input side — gate `_result_has_split_secret` on `scan_controls_configured` / the gated main scan. Resource guards run symmetrically on both input (`args_too_deeply_nested`) and output at 0 controls — defensible availability floors, keep. |

Why these weren't caught earlier: unit tests covered per-tool disable, chat-path scanning, and the
happy paths; none exercised 0-controls-skip, server-level disable, Tier-2 actually invoking Bedrock via
the MCP orchestrator, or per-org MCP rate limiting under the wrong-module import. The bare-`import main`
hazard was known and fixed for `_get_policy_sync` but not swept across siblings.

## 2. Invariants VERIFIED (no defect)

| Area | Verdict | Evidence |
|------|---------|----------|
| Multi-org / cross-org isolation | VERIFIED | 4 routes enforce `_validate_org_scope`; org_slug is sha256(key)→Redis-bound; caches keyed `{org}/{server}`; 413 live `org_scope_violation` blocks; live cross-org → 403 |
| Tier-2 stays OFF unless enabled | VERIFIED | 0 tier2 stages across 295k+ events; org `mcp_tier2_enabled` gating |
| Scan-control precedence (tool>server>org, priority, direction, disabled-override) | VERIFIED (w/ conflict caveat #24) | `SCOPE_RANK={tool:3,server:2,org:1}` + `max(sort_key=(scope_rank,priority))` in container source → correct direction; disabled rows stay candidates so narrow `enabled=False` overrides broad `enabled=True`; 10/10 executable probe + live matrix reversible. CAVEAT: **same-scope + same-priority conflicting rows resolve nondeterministically** (see #24) |
| Config → behavior mapping | VERIFIED | Live reversible: create server `block` control → same input flips monitor→block (effective_control_id ties to the row) → delete → reverts; scan-version cache invalidation |
| Transports (stdio/streamable-http/sse/websocket) | VERIFIED | All 4 have live connected servers + discovered tools; live tool calls per transport |
| Sandbox isolation | VERIFIED | runc hardened: ReadonlyRootfs, non-root, CapDrop ALL, no-new-privileges, own PID ns, per-org bridge net, cgroups (2GB/1CPU/256pid); idle reaper; MCP runs inside |
| Compliance-mapping "toggle" | FALSE PREMISE (evidence-deepened) | No on/off toggle exists. Two DISTINCT surfaces, both correct: (1) event-time `get_compliance_tags(threat_type)` (`policy/compliance.py`) is a pure static threat→regulation map (PII→GDPR/CCPA, SSN→HIPAA) — intrinsic threat classification, correctly NOT gated by org config; (2) `FirewallConfig.compliance_frameworks` is a POSTURE selector consumed by `core/compliance.py:validate_compliance_requirements` (gap-checks config vs each selected framework's requirements) + serializer (constrains `tier2_execution_mode` for strict frameworks) — real, consumed, not dead. Low-sev labeling note: disjoint vocab (selectable SOC2/ISO27001 vs emitted GDPR/CCPA/HIPAA/PCI-DSS) could mislead users into expecting selection to filter event tags; it does not (doc clarity, not a defect). |
| PII/secret not leaked into telemetry | VERIFIED | 0 raw secret/PII bytes in 295k `scan_findings` |
| OpenAI-SDK chat path (injection block, PII, streaming, malformed, concurrency) | VERIFIED | Live: injection→content_filter block; PII→output_blocked; streaming SSE; malformed→400; 5 parallel→distinct request_ids |

## 3. Remaining risks / open items

- **Gateway fixes #15/#17/#18/#19 are DEPLOYED (rebuild-from-working-tree) and LIVE-VERIFIED, but
  git-UNCOMMITTED** — commit blocked by co-mingling with a concurrent session's in-flight (still-red)
  observe-only refactor in `mcp_proxy.py`/`mcp_scan_orchestrator.py`. Action: once that refactor lands
  green, commit these four narrowly (their code + tests are ready). Until then they live only in the
  working tree/running image and would be lost on a clean checkout.
- **Sandbox runtime = runc, not gVisor:** intended (gVisor opt-in via `MCP_SANDBOX_RUNTIME=runsc` +
  `_REQUIRED=true`; a loud degraded-runtime warning fires). For max isolation in prod, enable runsc.
- **Sandbox egress open** (for npx/uvx fetch): a malicious package could attempt exfil; mitigated by
  firewall I/O scanning + per-org net + cap-drop, but not egress-filtered. Prod: add an egress policy.
- **`vector_routes.py:669`** — same bare-`import main` class (RAG path; has a partial fallback). Low.
- **Scan-control conflict determinism (#24):** recommended fix — (a) add a stable final tiebreak to
  `_pick_control`'s `sort_key` (e.g. append `control_id`) so ties resolve deterministically, and/or
  (b) resolve conflicts by **most-restrictive-wins** (block > redact > monitor > allow; `enabled=False`
  beats `enabled=True`) instead of arbitrary order, and (c) reject duplicate same-scope rows in
  `MCPScanControlSerializer.validate` and/or add a partial `UniqueConstraint`. All three sites are
  currently co-mingled; apply once the concurrent control-side refactor settles. Choice between (a) and
  (b) is a product/semantic decision (deferred to avoid FP over-reach).
- **`security_engines` items now FIXED (committed; live-verify pending control rebuild):** response-side
  PII-merge drop (#21 `d30c602b`), case-sensitive `context=="medical"` PHI skip (#22 `382d23de`),
  non-dict action-entry crash (#23 `0843fc91`). **Still OPEN (detection-tuning judgment, parallel-audit
  domain):** response-side LLM-detector blind spot (9/11 detectors only inspect `prompt`), goal-hijack
  `keyword_overlap==0` bypass, no unicode/spacing normalization (dead `pattern_matcher.py` has a better
  regex table), Bedrock 2000-char prompt truncation. Left OPEN deliberately: each is a false-positive-risk
  detection-tuning change, not a clear-cut crash/correctness fix.
- **Tier-2 enable propagation timing:** the FIRST tool call after enabling Tier-2 may see the pre-refetch
  `enabled_info` cache (a warmup call forces it); the underlying propagation is version-invalidated and
  sound (Redis `mcp:scan_ver` bumps on FirewallConfig save). Not a defect; a UX/latency note.

## 4. Production-readiness verdict

The MCP firewall's core guarantees hold and are **live-verified**: org isolation, off-by-default
scanning, scan-control precedence + matrix, all 4 transports + OAuth, sandbox isolation (runc-hardened +
default seccomp + cgroups + per-org net + command-allowlist + SSRF egress guard), Tier-1/Tier-2 gating,
the OpenAI-SDK enforcement path, DoS body-cap, full registration lifecycle, and cross-org/cross-server
isolation. **Defect ledger: 11 fixed, 3 documented-with-root-cause (#24 conflict determinism, #25 org Tier-2 toggle no-op, #26 residual 0-controls output floors).** Committed & (where the control
image was rebuilt) live-verified: #8 `c3d412f5`, #20 `36c4c1aa`, #21 `d30c602b`, #22 `382d23de`,
#23 `0843fc91`. Deployed + live-verified but git-uncommitted (co-mingling): gateway #1/#15/#17/#18/#19.
Verified standalone, live-verify pending the next control rebuild: #21/#22/#23. Documented with verified
root cause (fix co-mingled, deferred): #24 scan-control conflict nondeterminism. The **remaining blockers
to "fully ship-ready"** are all external-gated, not open questions: (a) commit the gateway fixes once the
concurrent refactor settles; (b) a control rebuild to live-verify #21–#23; (c) a product decision + clean
window to land the #24 conflict-determinism fix. Recommended prod hardening: `MCP_SANDBOX_RUNTIME=runsc`
+ sandbox egress policy; and close the open control-plane `security_engines` detection-tuning items above.
