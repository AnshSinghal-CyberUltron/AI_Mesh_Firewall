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
