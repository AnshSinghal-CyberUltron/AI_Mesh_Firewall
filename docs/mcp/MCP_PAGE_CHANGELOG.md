# MCP Page (firewall-1-4) Cleanup Changelog

Program `claude-mcp-page-cleanup` — fixing the still-broken Context Assembly & MCP page:
clean non-revealing errors at every leak site, resolve stuck "Unknown" servers, count
context-assembly redactions, correct the System-Status banner, and make the 1.4 page
aligned + responsive (impeccable). One item per iteration; every change four-memory logged
(Ruflo `mcp-page/changes` + this file + `.cursor/rules/mcp-page-changelog.mdc` + AGENTS.md).
Format: id | files | WHAT | WHY | NOW DOES | AFFECTS | VERIFY.

## MCP-PAGE-CLEANUP-00 — PAGE OWNERSHIP
- **files:** mcp-parallel/claims/claude-mcp-page-cleanup-C0.claim; docs/mcp/MCP_PAGE_CHANGELOG.md; .cursor/rules/mcp-page-changelog.mdc; AGENTS.md
- **WHAT:** claim the MCP page (?tab=firewall-1-4) frontend files (MCPConnectorPanel/MCPScanControlMatrix/PolicyManagementPanel + module-1.4 slices of useFirewallData/firewall-module-utils/firewall-submodules) and the MCP error/state backend (mcp_proxy.py discovery+transport error paths, mcp_stdio_adapter.py stdio-start, mcp_connector/views.py sync-error classifier).
- **WHY:** coordinate with parallel sessions so MCP-page fixes don't collide; Cursor's broader frontend/** and the chat-pipeline carve-out (ModelConnectionPanel/OutputPipelineTimeline) are respected.
- **NOW DOES:** other MCP-frontend sessions stand down on these files; this program logs to a distinct trail (Ruflo namespace mcp-page + MCP_PAGE_CHANGELOG.md).
- **AFFECTS:** coordination only; no code change.
- **VERIFY:** claim file present; ledger row appended to docs/mcp/PARALLEL_CLAIMS.md; on branch main HEAD.

## MCP-PAGE-CLEANUP-01 — gateway failure classifier
- **files:** gateway/ai_mesh_gateway/mcp_error_classifier.py (new); gateway/ai_mesh_gateway/tests/test_mcp_error_classifier.py (new)
- **WHAT:** a gateway-side failure classifier `classify_mcp_failure(exc=/status=/exit_code=/raw=)` → (stable client CODE, clean branded MESSAGE); `sanitize_mcp_error(...)` returns `{error,code,ref}` and records the raw cause dev-only.
- **WHY:** the gateway proxies discovery/tool-calls to sandboxes + external MCP hosts; its raw failures (exit codes, upstream HTML, hostnames in exceptions, stderr/'sandbox-agent logs') must never reach clients. The control plane already sanitizes its own sync path; the gateway needed the same at its transport boundary.
- **NOW DOES:** priority exit-code→HTTP-status→exception→raw-text. Covers OOM(-9/137), CRASH(-6/134/-11/139), START, AUTH(401/403), UPSTREAM_HTTP_ERROR (405→"error page (HTTP 405) — check the endpoint URL", 4xx/5xx echo the NUMBER only), TIMEOUT, DNS_FAILURE, CONNECTION_REFUSED, EGRESS/STORAGE/IMAGE/UNAVAILABLE. Codes mirror control's D-codes + 3 transport additions. Raw cause → structured WARNING log keyed by ref + best-effort shared-Redis mcp:diag:<ref> (7d).
- **AFFECTS:** building block only (no leak site routed yet — items 02-04). Imports httpx (present); redis.asyncio lazy + failure-swallowed.
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_error_classifier.py -q` → 33 passed (all named cases + leak-prevention asserts: no raw exc/host/HTML/'sandbox-agent'/exit-number in message; sanitize survives Redis-down).

## MCP-PAGE-CLEANUP-03 — stdio-start failures routed through the classifier
- **files:** gateway/ai_mesh_gateway/mcp_stdio_adapter.py; gateway/ai_mesh_gateway/tests/test_stdio_failure_message.py (new)
- **WHAT:** extracted `_stdio_failure_message(proc, rc, stderr_tail)` (async, testable) called from `_start_reader`'s finally; it builds the CLIENT-facing message via `sanitize_mcp_error`.
- **WHY:** the old `safe_msg` leaked the raw exit code `{rc}`, internal env-var names (MCP_SANDBOX_MEMORY_MB, MCP_STDIO_MAX_LINE_BYTES), the server key `'{proc.key}'`, and a "See gateway logs for details" pointer — all set on the pending futures → surfaced to the client / stored as last_sync_error.
- **NOW DOES:** message is exit-code-free, env-var-free, brand-safe, no server key. OOM (rc -9/137 or stderr 'heap out of memory' incl. V8-heap-SIGABRT-134, checked before the exit code)→'exceeded its memory limit'; crash(-6/134/-11/139)→'crashed while starting'; ENOSPC→storage; oversized-line→'response too large'; rc 0→missing-dependency; rc None→stdout-closed. Raw exit code/stderr/env-var hints stay in the WARNING logs + dev diagnostic keyed by ref.
- **AFFECTS:** the stdio (sandbox) transport error path; the message shown for a failing stdio server (ruflo OOM / cp08 / cp09 / stub bearer). Not yet deployed live (parallel session using the gateway; item 06 does the coordinated live verify).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_stdio_failure_message.py -q` → 11 passed (leak-prevention: no exit-code numbers / 'sandbox-agent'/'gateway logs' / MCP_SANDBOX* / stderr / secret / server key in the message); full suite 1770 passed.

## MCP-PAGE-CLEANUP-02 — discovery path routed through the classifier
- **files:** gateway/ai_mesh_gateway/mcp_proxy.py; gateway/ai_mesh_gateway/tests/test_mcp_discovery_clean_errors.py (new)
- **WHAT:** `internal_discover_tools` now returns CLEAN discovery errors via `sanitize_mcp_error` + the new `_discovery_error_response(clean)` helper (JSON-RPC message + top-level code + ref).
- **WHY:** the outer `except` returned `error.message = f"Upstream discovery failed: {exc}"` — dumping the raw exception (an internal hostname or a full upstream HTML error page) to the backend/client; and the non-SSE branch called `tools_resp.json()` which raises on an HTML page.
- **NOW DOES:** non-SSE branch checks `tools_resp.status_code >= 400` → clean "HTTP <status> — check the endpoint URL" (405 case) and guards `.json()`; the outer except classifies the exc (DNS/refused/timeout) into a branded message. Raw exc/HTML/hostname → log + dev diagnostic keyed by ref only.
- **AFFECTS:** the streamable-http/sse discovery path (backend tool-sync). Not yet deployed live (item 06 coordinated verify).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_discovery_clean_errors.py -q` → 3 passed (405 HTML→"HTTP 405"+code+ref, no raw HTML; DNS→MCP_DNS_FAILURE no host; refused→MCP_CONNECTION_REFUSED no IP); discovery-scan regression 5 passed; full suite 1773 passed.

## MCP-PAGE-CLEANUP-04 — transport (HTTP/SSE/WS) + OAuth 401 clean errors
- **files:** gateway/ai_mesh_gateway/mcp_proxy.py; gateway/ai_mesh_gateway/tests/test_mcp_ext_transport_clean_errors.py (new)
- **WHAT:** the external-passthrough transport failures + upstream 401/403 now route through the classifier.
- **WHY:** ext_mcp_proxy's `except` returned `f"DNS resolution failed for '{hostname}'"` + `detail=str(exc)` (leaks the operator hostname + raw exception) and `detail=str(exc)` on generic unreachable; the control-proxy `except` also leaked `str(exc)`; an upstream 401 was passed through (its body can hint at tokens/endpoints).
- **NOW DOES:** ext + control transport `except` → `sanitize_mcp_error(exc=exc)` → clean DNS/refused/timeout `{error,code,ref}` at 502 (hostname/exc → log+diag by ref only). NEW upstream 401/403 intercept → `sanitize_mcp_error(status=401)` → "needs re-authentication — re-authorize the connection" (raw upstream body not echoed).
- **AFFECTS:** the external HTTP/SSE passthrough + the control-proxy error path. Not yet deployed live (item 06).
- **VERIFY:** `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_transport_clean_errors.py -q` → 3 passed (DNS→MCP_DNS_FAILURE no host/Errno/no detail field; refused→MCP_CONNECTION_REFUSED no IP; 401→MCP_AUTH_FAILED re-auth, no token hint); ext regression 57 passed; full suite 1776 passed.

## MCP-PAGE-CLEANUP-05 — dev-only correlation-id → full-cause diagnostic (unified endpoint)
- **files:** control/ai_mesh_control/mcp_connector/views.py; gateway/ai_mesh_gateway/tests/test_mcp_error_classifier.py
- **WHAT:** control's staff-only `MCPDiagnosticDetailView` now resolves BOTH control- and gateway-originated diagnostics by ref, via new `_read_gateway_diagnostic(ref)`.
- **WHY:** the gateway classifier writes the full cause to the raw Redis key `mcp:diag:<ref>` (JSON), but django_redis prefixes keys (`cache:1:mcp:diag:<ref>`, pickled), so `cache.get` missed gateway diags — the staff endpoint could not surface them.
- **NOW DOES:** `cache.get(_diag_cache_key(ref)) or _read_gateway_diagnostic(ref)` — the fallback does a raw Redis read of `mcp:diag:<ref>` off the shared Redis. Plus the always-on structured-log channel (`sanitize_mcp_error` logs ref→full cause at WARNING). Both dev-only, never client-facing (IsAdminUser + internal logs).
- **AFFECTS:** the staff-only diagnostics endpoint. Control change goes live on the next control restart (item 06 coordinated deploy).
- **VERIFY:** write side pytest — `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_error_classifier.py -q` → 34 passed (writes key `mcp:diag:<ref>`, 7d TTL, JSON full cause with host, host absent from client body). Read side LIVE (docker exec control shell): raw read returned the code; `cache.get` returned None; django key was `cache:1:mcp:diag:probekey` (prefix mismatch confirmed).

## MCP-PAGE-CLEANUP-06 — live verify failing servers show clean errors (+ SSRF-reason leak fix)
- **files:** scripts/ralph/mcp_page_cleanup_item06_verify.py (new); gateway/ai_mesh_gateway/mcp_proxy.py (SSRF-reason fix — commit deferred, see note); tests updated.
- **WHAT:** deployed items 01-05 to the running gateway + live-verified that every failing server shows a clean, non-revealing error; fixed a bonus SSRF-reason leak found during verification.
- **NOW DOES:** re-syncing the real failing servers (cp08 was raw 405 HTML, Linear 401, bogus stdio) yields clean branded messages + correlation refs (no HTML/exc/exit-code/hostname). The SSRF-guard rejection (3 sites) no longer echoes the raw reason (DNS errno / resolved internal or 169.254.169.254 metadata IP) — routed through sanitize_mcp_error; raw → log+diag by ref.
- **AFFECTS:** the live gateway (deployed via docker cp + kill -HUP 1; healthy). Dev retrieves the full cause by ref (item 05).
- **VERIFY:** live re-sync via control API → clean errors w/ refs; `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` → 1782 passed 0 failed (test_ssrf_reject_dns_reason_is_clean + 2 ext-SSRF tests updated to assert no IP/reason leak).
- **NOTE:** mcp_proxy.py + its 2 test files' COMMIT is DEFERRED — a parallel session has an uncommitted SSE hunk in the same mcp_proxy.py; git add -p is blocked so selective staging isn't possible. The fix is deployed + test-green + live; source commits land when the file settles.

## MCP-PAGE-CLEANUP-07 — registration triggers discovery/sync (no more stuck "Unknown")
- **files:** control/ai_mesh_control/mcp_connector/views.py
- **WHAT:** the registration create (MCPServerListCreateView.post) now sets connection_status="syncing" + fires a background sync so the state resolves; new `_trigger_background_sync(server, org)` helper.
- **WHY:** connection_status only transitioned (connected/failed) via `_resync_server_tools` on POST /servers/<id>/tools/. The UI calls it, but non-UI/bulk registrations never did → servers lingered at "unknown, 0 tools, never synced".
- **NOW DOES:** register → status "syncing" (never "unknown") → daemon thread runs `_resync_server_tools` (gateway discover-tools + status/tool update) → connected/failed. Any registration (UI or script) auto-syncs. Thread manages its own DB connection (close_old_connections); failures swallowed+logged.
- **AFFECTS:** POST /api/mcp-connector/servers/ (registration). The UI's own inline /tools/ sync still runs (idempotent).
- **VERIFY:** LIVE (control gunicorn 16 uvicorn workers; deployed docker cp + kill -HUP 1, graceful; healthy + login 200): registered a stdio everything-server via API WITHOUT /tools/ → response connection_status="syncing" → resolved to "connected" 13 tools in ~2s. Never lingered at unknown.

## MCP-PAGE-CLEANUP-08 — per-server re-sync resolves the stuck "Unknown" servers
- **files:** (verification only — the re-sync action already exists) frontend/src/components/MCPConnectorPanel.jsx (syncServerTools); backend _resync_server_tools.
- **WHAT:** verified the per-server re-sync action works + used it to resolve every stuck "unknown" server.
- **WHY:** 12 servers (Everything 1-5, Linear Remote/MCP, Filesystem Canary, Stub Bearer/OAuth, probe) lingered at "unknown, 0 tools, never synced" from pre-item-07 bulk registration.
- **NOW DOES:** syncServerTools(id) → POST /servers/<id>/tools/ (buttons: aria-label "Sync tools from server" :1396, :1443; auto after retry/register). Re-syncing all 12 → every one left "unknown": 6 connected (Everything 1-5 + Filesystem Canary), 6 failed-with-CLEAN-errors (probe/Stub Bearer→unreachable, Stub OAuth→re-auth, Linear×3→rejected auth). 0 remain unknown.
- **AFFECTS:** server state resolution. With item 07 (registration auto-sync) + this re-sync button, no server can stay stuck at unknown.
- **VERIFY:** LIVE bulk re-sync via API → "STILL UNKNOWN: NONE"; failed servers show clean branded errors.

## MCP-PAGE-CLEANUP-09 — "Syncing" state renders (no fall-through to "Unknown") + page verify
- **files:** frontend/src/lib/mcpColors.js; scripts/ralph/mcp_page_cleanup_item09_verify.mjs (new)
- **WHAT:** added the "syncing"/"connecting" entries to CONNECTION_STATUS + Playwright page-level verification that no server sits at "Unknown".
- **WHY:** item 07 sets connection_status="syncing", but the frontend map had no such key → connectionInfo("syncing") fell through to `unknown` → rendered "Unknown" (the stuck state we're removing).
- **NOW DOES:** syncing→"Syncing" (amber, pulsing), connecting→"Connecting"; the state reads unknown→Syncing→Connected/Failed. Live page shows all servers resolved.
- **AFFECTS:** the connection-status badge on every MCP surface using connectionInfo.
- **VERIFY:** node connectionInfo("syncing")==="Syncing"; frontend build green; LIVE Playwright (both themes @1440+375) → cleanup09Pass:true (Connected=12 Failed=9 Unknown=0; hasNeverSynced=false; 0 console errors; no overflow).

## MCP-PAGE-CLEANUP-10 — impeccable audit of the 1.4 page layout
- **files:** docs/mcp/IMPECCABLE_AUDIT_cleanup_1-4.md (new)
- **WHAT:** /impeccable audit of the 1.4 page (stat-card grid, server list, simulator, traffic-path, evidence table) → health 14/16.
- **WHY:** Section C requires an audit before the alignment/responsiveness fixes.
- **NOW DOES:** documents the layout state — detector clean (0 anti-patterns), responsive (no overflow at 4 widths/2 themes, cards stack at 375), cards equal-height + aligned. Finding: stat-card secondary labels truncate mid-word at 1440; minor spacing variance.
- **AFFECTS:** documentation only. Backlog feeds items 11–14.
- **VERIFY:** detect.mjs exit 0; item-09 Playwright sweep noOverflow:true; read rendered PNGs (light-1440, dark-375). Report: docs/mcp/IMPECCABLE_AUDIT_cleanup_1-4.md.

## MCP-PAGE-CLEANUP-11 — stat-card truncation + equal-height alignment fix
- **files:** frontend/src/components/MCPConnectorPanel.jsx (StatCard)
- **WHAT:** stat-card labels no longer clip mid-word; cards are equal-height with values top-aligned.
- **WHY:** the audit (item 10) found "Servers connected"→"Servers connec…" and "244 redact · 0 monitor"→"…0 m…" clipping (truncate); items-center misaligned values across cards of different label lengths.
- **NOW DOES:** label/sub use leading-tight (wrap, no clip); CardContent items-start (values line up across cards); Card h-full → fills the stretch grid cell → equal heights.
- **AFFECTS:** the 6-card stat grid at the top of the 1.4 page.
- **VERIFY:** build green; LIVE Playwright (both themes @1440+375) 0 console errors / no overflow; read light-1440 PNG → labels wrap (no clip), 6 cards equal-height, values top-aligned.

## MCP-PAGE-CLEANUP-12 — responsiveness verified (both tabs, 4 widths) + touch-target bump
- **files:** frontend/src/components/MCPConnectorPanel.jsx; scripts/ralph/mcp_page_cleanup_item12_verify.mjs (new)
- **WHAT:** verified server list + evidence table reflow at all 4 widths/2 themes; bumped 4 on-page icon buttons to 36px.
- **WHY:** item 12 requires no overflow/clipping/overlap + mobile touch targets; 4 icon buttons were 28px (h-7 w-7).
- **NOW DOES:** h-7 w-7 → h-9 w-9 (gateway-key reveal/copy, copy-URL, dismiss-error). Harness sweeps servers + observability tabs; excludes the decorative hero-glow + global nav + inline text links (WCAG exempt).
- **AFFECTS:** the 1.4 page icon buttons; responsiveness verification.
- **VERIFY:** build green; LIVE Playwright cleanup12Pass:true (noOverflow, noBleed on both tabs all 8 combos, touchOk 24px WCAG 2.5.8 AA, 0 console errors). Evidence list = reflowing cards, stacks at 375.
