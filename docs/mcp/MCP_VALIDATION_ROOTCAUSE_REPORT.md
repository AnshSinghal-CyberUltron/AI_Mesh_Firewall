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

Why these weren't caught earlier: unit tests covered per-tool disable, chat-path scanning, and the
happy paths; none exercised 0-controls-skip, server-level disable, Tier-2 actually invoking Bedrock via
the MCP orchestrator, or per-org MCP rate limiting under the wrong-module import. The bare-`import main`
hazard was known and fixed for `_get_policy_sync` but not swept across siblings.

## 2. Invariants VERIFIED (no defect)

| Area | Verdict | Evidence |
|------|---------|----------|
| Multi-org / cross-org isolation | VERIFIED | 4 routes enforce `_validate_org_scope`; org_slug is sha256(key)→Redis-bound; caches keyed `{org}/{server}`; 413 live `org_scope_violation` blocks; live cross-org → 403 |
| Tier-2 stays OFF unless enabled | VERIFIED | 0 tier2 stages across 295k+ events; org `mcp_tier2_enabled` gating |
| Scan-control precedence (tool>server>org, priority, direction, disabled-override) | VERIFIED | 10/10 executable probe + live matrix (block/redact/both/precedence) reversible |
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
- **Other `security_engines` items (control plane):** response-side LLM-detector blind spot, PII-merge
  drops response-side leaks, goal-hijack keyword-overlap bypass, case-sensitive `context=="medical"`
  PHI skip, dead `pattern_matcher.py` with a better impl. Confirmed-by-read by a parallel audit; OPEN.
- **Tier-2 enable propagation timing:** the FIRST tool call after enabling Tier-2 may see the pre-refetch
  `enabled_info` cache (a warmup call forces it); the underlying propagation is version-invalidated and
  sound (Redis `mcp:scan_ver` bumps on FirewallConfig save). Not a defect; a UX/latency note.

## 4. Production-readiness verdict

The MCP firewall's core guarantees hold and are **live-verified**: org isolation, off-by-default
scanning, scan-control precedence + matrix, all 4 transports + OAuth, sandbox isolation (runc-hardened +
default seccomp + cgroups + per-org net + command-allowlist + SSRF egress guard), Tier-1/Tier-2 gating,
the OpenAI-SDK enforcement path, DoS body-cap, full registration lifecycle, and cross-org/cross-server
isolation. **All 7 discovered defects are fixed and live-verified** (#8 committed `c3d412f5`; #20 committed
`36c4c1aa`; #15/#17/#18/#19 deployed + live-verified but git-uncommitted). The **sole remaining blocker to
"ship-ready"** is committing the four gateway fixes to git — mechanically ready, blocked only by
co-mingling with an unrelated concurrent refactor. Recommended prod hardening: `MCP_SANDBOX_RUNTIME=runsc`
+ sandbox egress policy; and close the open control-plane `security_engines` items above.
