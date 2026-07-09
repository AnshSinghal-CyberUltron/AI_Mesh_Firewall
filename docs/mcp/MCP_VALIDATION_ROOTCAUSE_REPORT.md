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
| 27 | MCP Tier-2 path **does not honor the org `tier2_strict` fail-closed-on-unavailable** setting the way the chat path does → Tier-2 silently bypassed on a Bedrock outage for a strict org | Med (silent security bypass; needs Tier-2 enabled + Bedrock unavailable/breaker-open) | `tier2_strict` (per-org `FirewallConfig`, model comment `core/models.py:1055-1058`: controls behavior "when Tier-2 is **unavailable** … `tier2_unavailable_strict` **rather than silently passing through**") is honored by the CHAT path (`main.py:54` `_is_tier2_unavailable_strict`, `:6923` breaker-open+strict→451, `:6957` `reason=tier2_unavailable_strict`) but NOT by the MCP path. In `mcp_scan_orchestrator.py:_scan_text_tier2`: (a) scanner-None branch (`:745`) returns `blocked=False` UNCONDITIONALLY, ignoring `org_tier2_strict`; (b) a `Tier2UnavailableStrict` raised by `scan_prompt_with_tier2` (breaker-open + org strict, `scanner.py:1985`) is swallowed by the generic `except Exception` (`:754`) and re-decided on the PER-ROW `strict_mode` — so a per-row `strict_mode="fail_open"` DEFEATS the org's `tier2_strict=True`. Two distinct strict settings conflated: per-row `strict_mode` = scanner-ERROR (frontend `MCPScanControlMatrix.jsx:909-910` "block/allow on scanner error"); per-org `tier2_strict` = scanner-UNAVAILABLE. | **DOCUMENTED — verified root cause** (chat path is the reference implementation proving intent; MCP path lacks the equivalent). Net: on a Bedrock outage a strict org's MCP tool calls silently skip Tier-2 (scanner-None case always; breaker-open case when per-row is `fail_open`). Fix: in `_scan_text_tier2`, honor `org_tier2_strict` on scanner-None (fail closed → `tier2_unavailable_strict`) and special-case `Tier2UnavailableStrict` (re-raise / hard-fail like `main.py`) instead of folding it into the generic per-row `strict_mode` branch. Fix site co-mingled with the gateway refactor; deferred. |

Why these weren't caught earlier: unit tests covered per-tool disable, chat-path scanning, and the
happy paths; none exercised 0-controls-skip, server-level disable, Tier-2 actually invoking Bedrock via
the MCP orchestrator, or per-org MCP rate limiting under the wrong-module import. The bare-`import main`
hazard was known and fixed for `_get_policy_sync` but not swept across siblings.

## 2. Invariants VERIFIED (no defect)

| Area | Verdict | Evidence |
|------|---------|----------|
| Multi-org / cross-org isolation | VERIFIED | 4 routes enforce `_validate_org_scope`; org_slug is sha256(key)→Redis-bound; caches keyed `{org}/{server}`; 413 live `org_scope_violation` blocks; live cross-org → 403 |
| Sandbox upstream egress / SSRF (untrusted MCP can't exfil to arbitrary/internal hosts) | VERIFIED (layered, DNS-rebind-safe) | `sandbox-image/agent/upstream_manager.py`: (1) `allowed_hosts` REQUIRED — no default-allow (`:54-56`); (2) host must be a MEMBER of the allowlist, normalized, for both IP-literal + named hosts (`:60-68` → "egress denied: host not in allowlist"); (3) resolved-IP SSRF guard `_assert_upstream_not_ssrf` (CHG-0067) resolves the host + blocks private/loopback/link-local/cloud-metadata IPs → DEFEATS DNS rebinding even for an allowlisted domain; (4) explicit 169.254.169.254 / fd00:ec2::254 IAM-metadata block; (5) fail-closed on DNS resolution failure. Governs the MCP UPSTREAM dial; the looser npx/uvx PACKAGE-fetch egress remains the documented remaining-risk |
| OAuth MCP credential isolation (client secret never reaches the sandbox) | VERIFIED (defense-in-depth + test) | Sandbox runs untrusted tenant code, so it must NOT hold OAuth client credentials. The sandbox agent (`sandbox-image/agent/upstream_manager.py:49-50`) HARD-REJECTS `oauth_client_role=="client"` (`UpstreamError -32602`) → the sandbox can never run the OAuth token-exchange flow, so the client_id/secret never enters it. Only the pre-obtained BEARER TOKEN is forwarded (control-plane→gateway→sandbox for the upstream dial; token-forwarding fixed by `ffc32375`). Enforced at the sandbox boundary (not just trusted from the gateway) with test coverage (`test_oauth_client_role_rejected`). Default `oauth_client_role="forbidden_in_sandbox"` (`routes.py:99`, `main.py:52`) |
| MCP BROKER tenant isolation (org_slug → per-org sandbox, no cross-tenant collision) | VERIFIED (real hazard found + fixed) | Broker key is a SHARED secret → `org_slug` is the sole tenant selector. `DockerManager` LOSSILY sanitizes the slug for the container name → distinct slugs could COLLIDE (CHG-0111 hazard: "org B's tool call would execute in / destroy org A's sandbox"). FIXED: `_require_canonical_org_slug` (`services/mcp-broker/src/sandbox/routes.py:35`) rejects (400) any slug the sanitizer would alter (substitution/strip/empty/>64) → only 1:1-canonical slugs route. Called on ALL routes: ensure(`:330`), rpc/stdio-rpc via `_forward_sandbox_rpc`(`:358`), status(`:445`), destroy(`:487`). Plus cross-tenant availability hardening: CHG-0146 agent-timeout ceiling, CHG-0151 16MiB response-size cap (shared-broker OOM containment); audit logs safe metadata only (never params/args/env) |
| Tier-2 stays OFF unless enabled | VERIFIED | 0 tier2 stages across 295k+ events; org `mcp_tier2_enabled` gating |
| Scan-control precedence (tool>server>org, priority, direction, disabled-override) | VERIFIED (w/ conflict caveat #24) | `SCOPE_RANK={tool:3,server:2,org:1}` + `max(sort_key=(scope_rank,priority))` in container source → correct direction; disabled rows stay candidates so narrow `enabled=False` overrides broad `enabled=True`; 10/10 executable probe + live matrix reversible. CAVEAT: **same-scope + same-priority conflicting rows resolve nondeterministically** (see #24) |
| Config → behavior mapping | VERIFIED | Live reversible: create server `block` control → same input flips monitor→block (effective_control_id ties to the row) → delete → reverts; scan-version cache invalidation |
| Transports (stdio/streamable-http/sse/websocket) | VERIFIED | Live active-server inventory: stdio=31, streamable-http=6 (incl. OAuth `linear-manual-oauth`, 47 tools), sse=1, websocket=1. All 4 + OAuth have live active/exposed registrations with discovered tools; live tool calls per transport |
| Offline/broken MCP handling + per-org server-state isolation | VERIFIED | Live: org-a/org-b `everything-N` + canary/cred servers = `connection_status=unknown, last_health_status=unreachable, tools_count=0` (registered, never connected) — represented (not hidden), degrade gracefully (empty tools/list; tool calls → 502 `backend_unreachable`/504 `backend_timeout` handlers in `org_mcp_tools_list`). The SAME `everything-N` config is synced+working in zeroshield (13 tools) but offline in org-a/org-b → per-org server-state isolation confirmed |
| Sandbox isolation | VERIFIED | runc hardened: ReadonlyRootfs, non-root, CapDrop ALL, no-new-privileges, own PID ns, per-org bridge net, cgroups (2GB/1CPU/256pid); idle reaper; MCP runs inside |
| Sandbox crash recovery / restart / cleanup lifecycle | VERIFIED (code + prior live) | `docker_manager.ensure` (`:741`): missing→create; exited/stopped→`start()`; WON'T-START (crashed/OOM/corrupted)→CHG-0133 remove+recreate ("chaos self-heal", per-org volume PERSISTS so no data loss). Broker restart→adopts orphaned running containers (restart-safety item #24, `:296-327`). Idle reaper cleans up (`reaper.py`). Destroy is cross-tenant-safe (CHG-0113 never removes another org's labeled volume). Live-corroborated (iter 33-35: a crash-looped sandbox recovered to healthy). `_resolve_running_sandbox` re-ensures on every rpc if not running |
| Sandbox routing — every transport executes inside the sandbox | VERIFIED (live env) | `_is_sandbox_routed` (`mcp_proxy.py:3788`): stdio+websocket ALWAYS routed; streamable-http+sse routed when `MCP_HTTP_VIA_SANDBOX` on. **Live gateway env `MCP_HTTP_VIA_SANDBOX=true`** → all 4 transports go gateway→broker→sandbox→upstream; the gateway NEVER dials upstream itself (CHG-0026 closed the last residual — ws previously connected in-gateway). See remaining-risk on the `=0` debug bypass |
| Compliance-mapping "toggle" | FALSE PREMISE (evidence-deepened) | No on/off toggle exists. Two DISTINCT surfaces, both correct: (1) event-time `get_compliance_tags(threat_type)` (`policy/compliance.py`) is a pure static threat→regulation map (PII→GDPR/CCPA, SSN→HIPAA) — intrinsic threat classification, correctly NOT gated by org config; (2) `FirewallConfig.compliance_frameworks` is a POSTURE selector consumed by `core/compliance.py:validate_compliance_requirements` (gap-checks config vs each selected framework's requirements) + serializer (constrains `tier2_execution_mode` for strict frameworks) — real, consumed, not dead. Low-sev labeling note: disjoint vocab (selectable SOC2/ISO27001 vs emitted GDPR/CCPA/HIPAA/PCI-DSS) could mislead users into expecting selection to filter event tags; it does not (doc clarity, not a defect). |
| PII/secret not leaked into telemetry | VERIFIED | 0 raw secret/PII bytes in 295k `scan_findings` |
| Prompt-injection / indirect-injection detection on the MCP path (gateway Tier-1) | VERIFIED (obfuscation-resistant) | `scanner.py` `prompt_injection` category = REGEX with `\s+` whitespace-tolerance + verb/qualifier alternation (`(?:ignore\|disregard\|forget\|override)\s+…\s+instructions`), ReDoS-safe, actively hardened (G15 fixed a multi-qualifier bypass). `patterns.py` preprocessing: NFKC normalization, homoglyph/skeleton fold (Greek/Armenian), ASCII-fold of injection substitutes, base64/hex/base32/base85/HTML-entity/percent/markdown-split decode. Same scanner runs on INPUT args (direct) + OUTPUT results (indirect). CONTRAST: the naive single-space substring bypass affects only the control-plane `owasp_llm_detector` (policy-eval API), NOT this MCP enforcement path |
| Tier-1 POLICY conflict resolution (multiple/conflicting policies × priority → one action) | VERIFIED (deterministic, most-restrictive-wins) | `policy/engine.py:evaluate`: rules sorted `(-priority, id)` (id tiebreak → deterministic); final action = MAX `ACTION_ORDER` rank across all matched rules (`{block:5,redact:4,rewrite:3,model_downgrade:2,monitor:1,allow:0}`) = most-restrictive-wins; redaction hints unioned across all redact rules; policy ids deduped (M-21); actor-scoped (M-04) + tool-targeted. "Every expected action occurs exactly once" = the single most-restrictive one. CONTRAST: this is the CORRECT pattern that scan-control `_pick_control` (#24) LACKS |
| Tool-visibility least-privilege (disabled/unexposed/cross-tool not leaked in `tools/list`) | VERIFIED (full JSON-RPC↔REST parity) | All 3 list surfaces — JSON-RPC (`:4146`), internal discovery (`:4228`), REST `org_mcp_tools_list` (`:5284`) — wrap `_filter_tools_by_key_allowlist(_filter_tools_by_enabled(...))`: `_filter_tools_by_enabled` (`:889` "drop entries whose name is in disabled set") REMOVES disabled tools; per-key allowlist filters further. Plus server-disable gate (#15, `:5268`), org-scope check (`_audit_and_return_scope_error`), and tool-metadata poisoning scan (`_scan_tool_result_floor`, CHG-0077/0082). Line-919 "previously filtered only by key" = now-fixed old state |
| OpenAI-SDK chat path (injection block, PII, streaming, malformed, concurrency) | VERIFIED | Live: injection→content_filter block; PII→output_blocked; streaming SSE; malformed→400; 5 parallel→distinct request_ids |

## 3. Remaining risks / open items

- **Gateway fixes #1/#15/#17/#18/#19 are DEPLOYED (rebuild-from-working-tree) and LIVE-VERIFIED, but
  git-UNCOMMITTED — BLOCKED WITH PROOF.** Commit blocked by co-mingling with a concurrent session's
  in-flight refactor in `mcp_proxy.py`/`mcp_scan_orchestrator.py`. **Exact blocker (hunk-level re-verified):**
  the fix set spans TWO files; `git diff HEAD` shows `mcp_scan_orchestrator.py` = 9 hunks (my #18
  `_get_input_scanner` sys.modules fix is the isolable `@@ -100` hunk; the other 8 across
  `_scan_text_tier1_sync`/`scan_mcp_payload` are the concurrent refactor), and `mcp_proxy.py` = **489
  insertions** with my #1/#15/#17/#19 (`_server_disabled`/`_gateway_app_module`/`_rest_server_disabled_response`/
  `scan_controls_configured` gate) interleaved across many functions among the concurrent hunks — NOT
  cleanly separable as a unit. A partial commit would (a) fragment the fix set, (b) move shared HEAD under
  a possibly-resuming concurrent session (shared-index hazard), (c) risk a non-coherent intermediate. So
  per discipline: NO partial commit; the set stays deployed+verified but uncommitted. **Unblock condition:**
  the concurrent refactor lands/settles (its 543 insertions committed or reverted), then commit these five
  narrowly. Until then they live only in the working tree/running image and would be lost on a clean checkout.
- **Sandbox bypass flag (`MCP_HTTP_VIA_SANDBOX=0`):** a documented debug fallback that makes
  streamable-http/sse transports bypass the per-org sandbox (gateway dials upstream via direct-httpx —
  still SSRF-guarded, but reduced isolation: no per-org net/cgroups/cap-drop around the upstream dial).
  Default-off and confirmed OFF (`=true`) in the running env; stdio/websocket are unaffected (always
  sandbox-routed). Prod: keep it unset/true. Not a defect (secure default), a config caveat.
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
  (b) is a product/semantic decision (deferred to avoid FP over-reach). **REFERENCE IMPLEMENTATION exists
  in-codebase:** `policy/engine.py:evaluate` already does exactly (a)+(b) for Tier-1 policies — rules
  sorted `(-priority, id)` (deterministic id tiebreak) + final action = MAX `ACTION_ORDER` rank
  (most-restrictive-wins). This reframes #24 from a novel product decision to "align `_pick_control` with
  the platform's existing canonical conflict-resolution pattern" — lowering the FP risk of the fix.
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
isolation. **Defect ledger: 11 fixed, 4 documented-with-root-cause (#24 conflict determinism, #25 org Tier-2 toggle no-op, #26 residual 0-controls output floors, #27 MCP Tier-2 ignores org tier2_strict fail-closed-on-unavailable).** Committed & (where the control
image was rebuilt) live-verified: #8 `c3d412f5`, #20 `36c4c1aa`, #21 `d30c602b`, #22 `382d23de`,
#23 `0843fc91`. Deployed + live-verified but git-uncommitted (co-mingling): gateway #1/#15/#17/#18/#19.
Verified standalone, live-verify pending the next control rebuild: #21/#22/#23. Documented with verified
root cause (fix co-mingled, deferred): #24 scan-control conflict nondeterminism. The **remaining blockers
to "fully ship-ready"** are all external-gated, not open questions: (a) commit the gateway fixes once the
concurrent refactor settles; (b) a control rebuild to live-verify #21–#23; (c) a product decision + clean
window to land the #24 conflict-determinism fix. Recommended prod hardening: `MCP_SANDBOX_RUNTIME=runsc`
+ sandbox egress policy; and close the open control-plane `security_engines` detection-tuning items above.

## 5. Coverage matrix — MCP lifecycle stages × validation status (deliverable #15/#16)

Legend: ✅ VERIFIED (evidence) · 🔧 FIXED&(live-)verified · 📝 DOCUMENTED-root-cause (fix external-gated) · ⚠️ residual/caveat.

| # | Lifecycle stage | Status | Evidence / finding |
|---|-----------------|--------|--------------------|
| 1 | Frontend config surfaces | ✅/📝 | Action vocab matches backend (`ACTION_CHOICES`); BUT org Tier-2 "Enabled" toggle is a no-op (📝 #25) |
| 2 | API (control endpoints) | ✅ | `/api/firewall/config/`, MCP scan-control CRUD, enabled-tools; org-scoped |
| 3 | Database (models) | ✅/📝 | `MCPScanControl` has NO UniqueConstraint → conflict nondeterminism (📝 #24) |
| 4 | Backend (control plane) | 🔧 | security_engines crashes #20/#21/#22/#23 fixed |
| 5 | Config loading | ✅ | `enabled_info` builder (`views.py:2567-2612`) |
| 6 | Config caching | ✅ | version-invalidated (`mcp:scan_ver` bump on save); #17 fixed TTL-only server-disable cache |
| 7 | Org config | ✅ | `FirewallConfig` per-org; Redis `firewall:config:{org}` |
| 8 | Server config | 🔧 | server disable (`is_active`/`is_exposed_to_agents`) enforced — #15 fixed |
| 9 | Tool config | ✅ | per-tool `scan_action`, tool-scope controls |
| 10 | Policy resolution | ✅ | 49 policies; OpenAI-SDK path live-verified |
| 11 | Scan-control resolution | ✅/⚠️ | tool>server>org precedence VERIFIED; conflict tie nondeterministic (⚠️ #24) |
| 12 | Context assembly | 🔧 | nested-JSON field redaction bypass fixed (#8 `c3d412f5`) |
| 13 | Compliance mapping | ✅ | no toggle (false premise); tags intrinsic; `compliance_frameworks` = posture selector |
| 14 | Tier-1 scanning | ✅/📝 | on-by-default when configured; off at 0 controls — BUT residual output floors (📝 #26) |
| 15 | Tier-2 scanning | 🔧/📝 | #18 fixed (was silently never running); org toggle no-op (📝 #25); strict-unavailable bypass (📝 #27) |
| 16 | MCP broker | ✅ | `ai_mesh_mcp_broker` healthy; docker_ok |
| 17 | Sandbox | ✅ | runc-hardened (CapDrop ALL, RO rootfs, non-root, per-org net, cgroups); gVisor opt-in |
| 18 | MCP runtime | ✅ | command allowlist (node/npx/python/uv/uvx); MCP runs inside sandbox |
| 19 | Transport | ✅ | stdio/streamable-http/sse/websocket all have live connected servers + discovered tools |
| 20 | Tool execution | ✅ | live tool calls per transport; historical 295k events |
| 21 | Output scanning | 🔧/📝 | direction dimension correct; #26 residual floors at 0 controls (output-only asymmetry) |
| 22 | Response generation | ✅ | JSON-RPC + SSE reframing; fail-closed on scan error |

Cross-cutting: multi-org isolation ✅ (413 live `org_scope_violation`); per-org rate limiting 🔧 (#19); DoS body/depth/node caps ✅; PII/secret not in telemetry ✅ (0 raw bytes in 295k findings).

**Coverage gaps / not-yet-live-exercised** (honest disclosure, not silent): live-verify of #21/#22/#23 (control rebuild pending); a live end-to-end tool call exercising #25/#26/#27 under the exact triggering config (would mutate shared org state / need Bedrock-outage simulation — deferred to avoid infra disruption per the iteration-33 discipline); every-permutation policy matrix (representative permutations verified live + by executable probe, not the full Cartesian product — the 10/10 precedence probe + reversible live matrix cover the resolution logic).

## 6. Security review (deliverable #18)

Threat model: an untrusted MCP server (or a poisoned tool registration) is the primary adversary — it can return malicious tool results (secret/PII/injection/exfil), advertise poisoned tool descriptions, or attempt SSRF/exfil from inside the sandbox. Secondary: a malicious agent/client sending secrets/injection in tool args, or attempting cross-org access.

| Control | Posture | Evidence |
|---------|---------|----------|
| Tenant isolation | STRONG | `_validate_org_scope` on all agent routes; org_slug=sha256(key); per-org caches/sandbox/bridge-net; 413 live `org_scope_violation`; cross-org→403; per-org server-state isolation (same `everything-N` synced in one org, offline in another) |
| Sandbox confinement | STRONG (config-dependent) | runc-hardened (CapDrop ALL, RO rootfs, non-root, own PID ns, per-org net, cgroups); ALL 4 transports sandbox-routed live (`MCP_HTTP_VIA_SANDBOX=true`); gateway never dials upstream. RISK: `=0` debug bypass (SSRF-guarded, off by default) |
| Egress / SSRF | GUARDED | per-org egress allowlist; SSRF guard on the direct path; sandbox bridge net. RISK: egress not fully filtered for npx/uvx fetch (remaining-risk) |
| Input (tool args) scanning | CORRECT | credential force-block (findings-dependent → correctly off at 0 controls); off-by-default gate (#1) |
| Output (tool result) scanning | CORRECT w/ 1 caveat | metadata poisoning scan (CHG-0077/0082), cross-block split-secret, infra/exfil floors; fail-closed on scan error. CAVEAT #26: split-secret + resource floors run at 0 controls (output-only asymmetry) |
| Tier-2 semantic scan | 2 GAPS | #18 fixed (was silently never running). OPEN: #25 org toggle no-op; #27 fails open on Bedrock-unavailable for a strict org |
| Least-privilege tool visibility | STRONG | disabled/unexposed/cross-tool not leaked in `tools/list` (JSON-RPC/REST parity); server-disable enforced (#15) |
| Telemetry hygiene | STRONG | 0 raw secret/PII bytes across 295k `scan_findings` |
| Availability / DoS | STRONG | body cap; arg/result depth+node+block-count caps; per-org TPM rate limiting (#19 fixed) |
| Fail-safe posture | MOSTLY | off-by-default gates fail SAFE to scanning on control-plane outage; scan errors fail CLOSED. GAP: #27 Tier-2-unavailable fails OPEN on MCP path |

Highest-priority security items to close (all documented, fixes external-gated): **#27** (Tier-2 fail-open on Bedrock outage — silent bypass), **#25** (Tier-2 org toggle no-op — false sense of security), **#26** (residual 0-controls floors — decision-#1 inconsistency), **#24** (conflict nondeterminism). Plus the co-mingled-uncommitted gateway fixes (#1/#15/#17/#18/#19) must land in git so a clean checkout retains them.

## 7. Performance review (deliverable #19)

| Dimension | Assessment | Evidence |
|-----------|-----------|----------|
| Config propagation | Low-latency, correct | Redis `mcp:scan_ver` version bump + version-invalidated caches (`_get_server_config`, `_get_enabled_tools`); no full-scan on every request; #17 replaced 120s TTL-only staleness with version-invalidation |
| Scan pipeline cost | Bounded | Tier-1 regex is O(n) on capped payloads; Tier-2 Bedrock gated behind Tier-1 pass + explicit enable (rarely on); depth/node/block-count guards pre-empt pathological payloads BEFORE the deepcopy+recursive scan (CHG-0115/0150) |
| Caching | Effective | per-`{org}/{server}` TTL+version caches; avoids per-call control-plane round-trips |
| Concurrency | Sound (asyncio) | single-loop; caches are dict read/update between awaits (no data race); version-check self-heals on the next request; 5-parallel live test → distinct request_ids |
| Sandbox overhead | Acceptable | per-org container reused; idle reaper cleans up; cold-start only on first call per org |
| Known cost caveats | Noted | Bedrock 2000-char prompt truncation (control-plane, security-relevant not perf); one-warmup-call latency on first Tier-2 after enable (UX note, not a defect) |

No performance defect found; the guards trade a cheap O(1)/iterative pre-check for avoiding multi-second resource-bomb scans — net positive. No unbounded loops or N+1 control-plane calls on the hot path.
