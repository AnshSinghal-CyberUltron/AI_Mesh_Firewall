# Claude Code Ralph — MCP page still-broken pass. ONE item/iteration. Log every change to 4 memories. Never fake green.

## C0 — Coordination
- [x] 00. Post PAGE-OWNERSHIP to the 4 memories (claim MCP-page files); read AGENTS.md/.cursor/rules/changelog. Rebase. DONE. Claimed the MCP page (?tab=firewall-1-4) frontend (MCPConnectorPanel/MCPScanControlMatrix/PolicyManagementPanel + module-1.4 slices of useFirewallData/firewall-module-utils/firewall-submodules) + MCP error/state backend (mcp_proxy.py discovery+transport error paths, mcp_stdio_adapter.py stdio-start, mcp_connector/views.py sync classifier). Posted to ALL 4: Ruflo (namespace mcp-page key changes/00-ownership + hooks_notify all/high), docs/mcp/MCP_PAGE_CHANGELOG.md (new), .cursor/rules/mcp-page-changelog.mdc (new), AGENTS.md; claim file mcp-parallel/claims/claude-mcp-page-cleanup-C0.claim + ledger row in docs/mcp/PARALLEL_CLAIMS.md. Coordination: Cursor owns broad frontend/**, chat-pipeline session owns ModelConnectionPanel/OutputPipelineTimeline — both explicitly avoid MCP files, so no conflict. On branch main HEAD (no feature-branch rebase needed). NOTE for item 01: the prior mcp-hardening program already built an MCP error taxonomy (D-codes MCP_AUTH_FAILED/MCP_OUT_OF_MEMORY/MCP_SERVER_CRASHED/... in control views _classify_sync_error + _classify_exit_reason in stdio_manager) — the new classifier must reconcile with/extend those, and item 02-04 must check whether mcp_proxy discovery + mcp_stdio_adapter still leak raw {exc}/HTML/exit-codes (line numbers shifted after CP49 +64 lines to mcp_proxy.py).

## A. Clean errors at EVERY leak site (top priority — the HTML dump is unacceptable)
- [x] 01. Build a failure classifier: process-crash (-6 SIGABRT / -11 SEGV / -9 OOM), start-timeout, DNS(-2), conn-refused, HTTP 4xx/5xx, auth 401 → {clean client message, stable client error code, correlation id}. DONE. New module `gateway/ai_mesh_gateway/mcp_error_classifier.py`: `classify_mcp_failure(exc=/status=/exit_code=/raw=)` → (stable CODE, clean MESSAGE) with priority exit-code → HTTP-status → transport-exception → raw-text fingerprint. Covers: OOM (-9/137/SIGKILL), CRASH (-6/134 SIGABRT + -11/139 SIGSEGV), START (other nonzero), AUTH (401/403), UPSTREAM_HTTP_ERROR (405→"returned an error page (HTTP 405) — check the endpoint URL", generic 4xx/5xx echo the NUMBER only), TIMEOUT, DNS_FAILURE (getaddrinfo/errno-2→"could not reach the server — check the URL/host"), CONNECTION_REFUSED (errno111), EGRESS/STORAGE/IMAGE/UNAVAILABLE. Codes MIRROR control's D-codes (MCP_AUTH_FAILED/OOM/CRASH/… so a client sees consistent codes cross-service) + 3 transport additions (DNS_FAILURE/CONNECTION_REFUSED/UPSTREAM_HTTP_ERROR). `sanitize_mcp_error(...)` (async) + `sanitize_mcp_error_sync(...)` → client-safe {error,code,ref}; the REAL cause goes ONLY to a structured WARNING log keyed by ref AND a best-effort shared-Redis write `mcp:diag:<ref>` (7d TTL, same convention as control's _store_sync_diagnostic — gateway+control share redis:6379/0, so item-05 debug view can retrieve gateway-originated diagnostics). NEVER emits exit codes, HTML/bodies, hostnames, or 'sandbox-agent logs' to clients (explicitly unit-tested). VERIFY: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_error_classifier.py -q` → 33 passed (all named cases + leak-prevention: message never contains raw exc/host/HTML/'sandbox-agent'/exit-number; sanitize returns {error,code,ref} 12-hex, survives Redis-down). Items 02-04 route the discovery/stdio/transport/OAuth leak sites through this. Four-memory logged.
- [ ] 02. Route the DISCOVERY path (mcp_proxy.py:1286) through the classifier — NEVER emit raw {exc}/HTML/body; the 405 case shows "The server returned an error page (HTTP 405) — check the endpoint URL", nothing more.
- [ ] 03. Route the STDIO-start path (mcp_stdio_adapter.py:306) through it — NEVER leak exit codes or "sandbox-agent logs"; -9→"server exceeded its memory limit", -6/-11→"the server failed to start".
- [ ] 04. Route the HTTP/SSE/WS transport + OAuth 401 errors through it — 401→"needs re-authentication" + the re-auth action; DNS/refused→"could not reach the server — check the URL/host".
- [ ] 05. DEV diagnostic: a correlation id maps to the FULL cause (exit code, stderr, upstream body, host) in dev-only logs / a debug endpoint — never shown to clients.
- [ ] 06. Verify on the live page: every failing server (ruflo/cp08/cp09/stubs/linear) shows a clean, human, non-revealing error; the dev can still retrieve the real cause via the id.

## B. Fix stuck "Unknown" servers + reliable sync
- [ ] 07. Registration triggers discovery/sync (inline or a background job); state goes unknown→connecting→connected/failed and NEVER lingers at "Unknown".
- [ ] 08. Per-server "re-sync/retry" action; the stuck Everything 1-5 / Linear Remote / Filesystem Canary / Stub Bearer resolve to a real state.
- [ ] 09. Verify: no server sits at "Unknown, 0 tools, never synced"; each shows connecting→resolved.

## C. Frontend alignment + responsiveness (Impeccable — the new ask)
- [ ] 10. /impeccable init + audit the whole 1.4 page layout (stat-card grid, server list, simulator, traffic-path, evidence table).
- [ ] 11. Fix ALIGNMENT: consistent grid/columns, equal card heights, aligned labels/values, consistent spacing/padding across all sections.
- [ ] 12. Fix RESPONSIVENESS: sections stack/reflow cleanly at 1440/1024/768/375; the server list + evidence table scroll or reflow (no overflow/clipping/overlap); touch targets fine on mobile.
- [ ] 13. Impeccable-revamp the page for a clean, aligned, intentional look; detector clean; both themes.
- [ ] 14. Verify (Playwright) alignment + responsiveness at all 4 widths with before/after screenshots; zero console errors.

## D. Context-assembly redactions = 0
- [ ] 15. Diagnose why "PII Redaction 0 sanitized" with 4,000 fields: is field-level redaction running in the context-assembly path? is the counter wired?
- [ ] 16. Fix so redaction runs on PII-bearing context/tool-output AND the sanitized counter reflects reality; verify with PII-carrying traffic (count > 0, fields actually masked).

## E. Backend-unreachable banner
- [ ] 17. Diagnose the System Status health check (timeout under load vs endpoint bug vs wrong URL); fix so it reflects real backend state.
- [ ] 18. Verify the banner shows Online when the backend is up; degrades gracefully (not a hard "unreachable") on slow responses.

## F. Policy Simulator
- [ ] 19. Default the simulator to a CONNECTED server (e.g. Everything MCP) with real tools, not a Failed one; handle "no tools" gracefully with guidance.
- [ ] 20. Verify dry-run + live-call work against a connected server and show decision/matched policies/rules.

## G. Retest fleet + verify
- [ ] 21. Re-test the 10 named MCPs + the 4-transport stubs: connected ones work; unreachable/misconfigured ones show CLEAN errors (no raw leak); OOM-heavy ones (ruflo) either get a raised limit or a clean memory error.
- [ ] 22. Every button/tab on the page (Tool Discovery/Execution/Scan Controls/Security Policies/Observability, per-server actions) works with real data, both themes, all widths, zero console errors.

## H. Freeze
- [ ] 23. Playwright snapshot gate (both themes + 375/768/1024/1440) so alignment/responsive can't regress; re-verify A–G 3×; all changes logged to 4 memories → <promise>COMPLETE</promise>.