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
| 15 | Disabling a server did not block access (`is_active`/`is_exposed_to_agents` unenforced) | Med-High (access control) | Flags stored + version-bumped but never consumed for gating; only per-*tool* disable was enforced | **FIXED (uncommitted)** — `_server_disabled` gate on all 6 gateway surfaces (JSON-RPC, 3 REST, 2 internal). 10 tests. Live: gate deployed, but see #17. |
| 17 | Server-disable was eventually-consistent (≤120s) | Med (window of exposure) | `_get_server_config` was TTL-only cached (120s), not version-invalidated, so `_server_disabled` read stale `is_active=True` | **FIXED (uncommitted)** — version-invalidate `_get_server_config` via `_current_scan_version` (mirrors `_get_enabled_tools`). Live-proven: cold cache blocks at T+0s, warm cache lagged. 2 tests. |
| 18 | MCP Tier-2 silently never ran (Bedrock scan skipped even when enabled) | Med-High (false security) | `_get_input_scanner()` used bare `import main` → resolved a module whose startup-set `INPUT_SCANNER` was None → `scan_prompt_with_tier2` returned `scanner_unavailable` (fail-open) | **FIXED (uncommitted)** — resolve via `sys.modules` (mirrors the already-fixed `_get_policy_sync`). Live evidence: trace `[('tier2','scanner_unavailable')]`; chat scanner works (INPUT_SCANNER set on app module). 3 tests. |
| 19 | Same bare-`import main` class systemic: MCP per-org rate limiting bypassed | Med-High (DoS/abuse) | `_mcp_org_rate_limit_raw` → `_enforce_org_tpm_rate_limit(rate_limiter=RATE_LIMITER)`; RATE_LIMITER None on the bare module → `enforce_org_tpm_rate_limit` no-ops. Also 2 CONFIG-flag helpers (low, fail-secure) | **FIXED (uncommitted)** — shared `_gateway_app_module()` resolver used at all 3 sites. 2 tests. Transitive live proof via #18. |

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

- **Uncommitted fixes (#15/#17/#18/#19):** live only after the next image rebuild; commit blocked by
  co-mingling with concurrent sessions in `mcp_proxy.py`/`mcp_scan_orchestrator.py`. Recommend: land the
  concurrent observe-only refactor, then commit these narrowly and rebuild to live-verify.
- **Sandbox runtime = runc, not gVisor:** intended (gVisor is opt-in via `MCP_SANDBOX_RUNTIME=runsc` +
  `_REQUIRED=true`); a loud degraded-runtime warning fires. For max isolation in prod, enable runsc.
- **Sandbox egress open** (for npx/uvx fetch): a malicious package could attempt exfil; mitigated by
  firewall I/O scanning + per-org net + cap-drop, but not egress-filtered.
- **`vector_routes.py:669`** — same bare-`import main` class (RAG path; has a partial fallback). Low.
- **Tier-2 org-gate precedence live test** — inconclusive due to config-cache lag; re-run after #17
  deploys.
- **Concurrent observe-only (`tag`/`monitor`) refactor** is mid-flight (some new tests red); its
  `tag`-observe-only semantics change PII masking under the default posture (tracked, not a new bug).

## 4. Production-readiness verdict

The MCP firewall's core guarantees hold and are live-verified: org isolation, off-by-default scanning,
scan-control precedence, transport coverage, sandbox isolation, and the OpenAI-SDK enforcement path.
**Not production-ready until** the four uncommitted enforcement fixes (#15 server-disable, #17 cache
timeliness, #18 Tier-2 actually running, #19 rate-limit bypass) are committed, rebuilt, and
live-verified, AND the concurrent observe-only refactor lands green. #8 (data-leak) is already shipped.
Recommended prod hardening: `MCP_SANDBOX_RUNTIME=runsc` + sandbox egress policy.
