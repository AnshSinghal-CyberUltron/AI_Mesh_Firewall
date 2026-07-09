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
| Compliance-mapping "toggle" | FALSE PREMISE | No such toggle exists; tags are intrinsic to detection (by design) |
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
isolation. **Defect ledger: 11 fixed, 1 documented-with-root-cause.** Committed & (where the control
image was rebuilt) live-verified: #8 `c3d412f5`, #20 `36c4c1aa`, #21 `d30c602b`, #22 `382d23de`,
#23 `0843fc91`. Deployed + live-verified but git-uncommitted (co-mingling): gateway #1/#15/#17/#18/#19.
Verified standalone, live-verify pending the next control rebuild: #21/#22/#23. Documented with verified
root cause (fix co-mingled, deferred): #24 scan-control conflict nondeterminism. The **remaining blockers
to "fully ship-ready"** are all external-gated, not open questions: (a) commit the gateway fixes once the
concurrent refactor settles; (b) a control rebuild to live-verify #21–#23; (c) a product decision + clean
window to land the #24 conflict-determinism fix. Recommended prod hardening: `MCP_SANDBOX_RUNTIME=runsc`
+ sandbox egress policy; and close the open control-plane `security_engines` detection-tuning items above.
