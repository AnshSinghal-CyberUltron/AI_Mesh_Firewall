---
iteration: 0
max_iterations: 50
completion_promise: COMPLETE
status: ACTIVE
---

# Cursor Ralph — MCP transports→sandbox + frontend (PARALLEL with a Claude Code session)

## Coordination (do every iteration)
- [x] C0. Join hive; read docs/mcp/PARALLEL_CLAIMS.md + mcp-parallel/claims. Only touch files in the
      Cursor-owned set (frontend/**, sandbox-image/agent/**, docker_manager.py, docs/). Claim before edit.
      **Done 2026-07-02:** joined hive-1782976205971-u1gav1 as cursor-ralph-iter2; ownership broadcast.

## P1 — Playwright-first exploration: WHY is MCP not working (repro the 4 UI-visible bugs)
- [x] 1. Playwright MCP: log in, open MCPConnectorPanel → register Linear via STDIO; capture the TWO
      auth buttons + "OAuth authorize failed: Server has no URL; OAuth is only for HTTP transports".
      **Verified 2026-07-02:** B1 NOT reproducing — 1 Authorize button, no no-URL error; see
      `mcp-parallel/findings/p1-1/`. OAuth start 401 to prod gateway URL is a separate env issue.
- [x] 2. Playwright: register an HTTP oauth server → confirm it LISTS immediately with 0 tools (wrong).
      **Verified 2026-07-02:** B2 partial — lists 0 tools while `oauth_authorized=false`; pending cues
      (`authorization required`, Authorize, sync disabled) present; see `mcp-parallel/findings/p1-2/`.
- [x] 3. Playwright: type into the Add-Server dialog fields → capture focus loss after 1 keystroke.
      **Verified 2026-07-02:** B4 NOT reproducing — focus kept for 24 keystrokes × 5 fields; prior
      `Dialog.jsx` onCloseRef fix effective; see `mcp-parallel/findings/p1-3/`.
- [x] 4. Playwright + logs: trigger a tool call that surfaces "MCP sandbox is temporarily unavailable";
      capture the network trace + broker logs. Record all repros to mcp-parallel/findings with screenshots.
      **Verified 2026-07-02:** B3 confirmed — broker stopped → tools/call shows error in UI + gateway
      logs (5× broker unreachable); see `mcp-parallel/findings/p1-4/`.

## P2 — OSS + internet research (better approaches for ALL-transport sandboxing)
- [ ] 5. GitHub MCP: study modelcontextprotocol/servers + how remote (http/sse/ws) MCPs are proxied;
      study mcp-remote (stdio-wraps-remote + OAuth) — clarifies Linear.
- [ ] 6. GitHub MCP: study gVisor (google/gvisor runsc) + Docker sandbox hardening for untrusted code
      (seccomp, no-new-privileges, cap_drop, read-only rootfs, egress allow-listing) + per-tenant patterns.
- [ ] 7. Web research: MCP OAuth 2.1 (PKCE, RFC 9728/8414) + why oauth needs an HTTP URL; correct UX for
      stdio-wrapped-remote; running http/ws clients inside a sandboxed agent. Record to mcp-parallel/findings.

## P3 — Contract-first seam (with the Claude session)
- [ ] 8. Draft docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md: the sandbox-agent endpoints for http/ws/sse proxy
      (request/response shape, streaming, timeouts, per-server config, egress policy). Ratify via
      hive-mind_consensus BEFORE implementing across the seam.

## P4 — Architecture: move ALL transports into the per-org gVisor sandbox
- [ ] 9. Extend sandbox-image/agent to proxy HTTP/SSE MCP servers (in-sandbox httpx client, egress
      allow-listed to the registered upstream only) — per the contract.
- [ ] 10. Extend sandbox-image/agent to proxy WEBSOCKET MCP servers (in-sandbox ws client) — per the contract.
- [ ] 11. Keep stdio as-is but unify: the agent exposes one transport-agnostic /rpc so the gateway calls
       the sandbox for EVERY transport; the gateway never dials upstream directly.
- [ ] 12. Enforce gVisor: docker_manager requires runtime=runsc in prod (fail-closed if unavailable);
       add security_opt (seccomp, no-new-privileges), cap_drop=ALL, tmpfs-only writes, egress lockdown.
- [ ] 13. Verify (Playwright + harness): with the gateway pointed only at the sandbox, an http, an sse, a
       ws, and a stdio MCP all work end-to-end THROUGH the sandbox; confirm the gateway opens NO direct
       upstream connection (network assertion) — nothing runs in the main backend.

## P5 — Frontend fixes (Playwright-verified; the dialog especially)
- [ ] 14. B1: OAuth selectable ONLY for HTTP transports; block oauth+stdio in the form; render exactly
       ONE Authorize button (HTTP+oauth only); remove the dup/broken control authorize path.
- [ ] 15. B2: freshly-registered HTTP oauth server shows a distinct "Pending authorization" state (not a
       0-tools card); tools appear only after oauth_authorized + sync.
- [ ] 16. B4: stabilize the Add-Server dialog so controlled inputs KEEP focus per keystroke (fix the
       remount: no field-component defined in render / stable keys / portal children not recreated).
- [ ] 17. Playwright verify B1/B2/B4 end-to-end with screenshots (Linear-stdio → no OAuth; HTTP-oauth →
       one Authorize + pending state → tools populate; type long strings in every dialog field, focus kept).

## P6 — Integrate + verify with the parallel session
- [ ] 18. Pull the Claude branch's gateway+broker changes (via the contract); run an integration check:
       all 4 transports through the sandbox under the Claude session's 15-MCP harness (concurrency/load/
       leakage). Fix any seam mismatch on the Cursor-owned side only.
- [ ] 19. Re-run P1 repros → all four UI bugs gone; re-run P4 verification 3× (in-process + live).
       When P1–P6 all [x] AND integration green, output <promise>COMPLETE</promise>.
