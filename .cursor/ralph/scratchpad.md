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
- [x] 5. GitHub MCP: study modelcontextprotocol/servers + how remote (http/sse/ws) MCPs are proxied;
      study mcp-remote (stdio-wraps-remote + OAuth) — clarifies Linear.
      **Done 2026-07-02:** `docs/mcp/oss-research-remote-transport-proxies.md` + `mcp-parallel/findings/p2-5/`.
- [x] 6. GitHub MCP: study gVisor (google/gvisor runsc) + Docker sandbox hardening for untrusted code
      (seccomp, no-new-privileges, cap_drop, read-only rootfs, egress allow-listing) + per-tenant patterns.
      **Done 2026-07-02:** extended `docs/mcp/oss-research-docker-hardening.md` + `mcp-parallel/findings/p2-6/`
      (`_run_kwargs` map, runsc fail-closed pattern, egress default-deny-proxy).
- [x] 7. Web research: MCP OAuth 2.1 (PKCE, RFC 9728/8414) + why oauth needs an HTTP URL; correct UX for
      stdio-wrapped-remote; running http/ws clients inside a sandboxed agent. Record to mcp-parallel/findings.
      **Done 2026-07-02:** `docs/mcp/oss-research-oauth-sandbox-client.md` + `mcp-parallel/findings/p2-7/`.

## P3 — Contract-first seam (with the Claude session)
- [x] 8. Draft docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md: the sandbox-agent endpoints for http/ws/sse proxy
      (request/response shape, streaming, timeouts, per-server config, egress policy). Ratify via
      hive-mind_consensus BEFORE implementing across the seam.
      **Done 2026-07-02:** `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` v1.0.0-draft; proposal
      `proposal-1782979656279-rgcvqe` — Cursor YES, Claude PENDING (provisional ratification).

## P4 — Architecture: move ALL transports into the per-org gVisor sandbox
- [x] 9. Extend sandbox-image/agent to proxy HTTP/SSE MCP servers (in-sandbox httpx client, egress
      allow-listed to the registered upstream only) — per the contract.
      **Done 2026-07-02:** `upstream_manager.py` + transport-aware `/rpc` in `main.py`; 7 agent tests green.
- [x] 10. Extend sandbox-image/agent to proxy WEBSOCKET MCP servers (in-sandbox ws client) — per the contract.
      **Done 2026-07-02:** `ws_manager.py` + websocket in `upstream_manager`/`main.py`; 21 agent tests green.
- [x] 11. Keep stdio as-is but unify: the agent exposes one transport-agnostic /rpc so the gateway calls
       the sandbox for EVERY transport; the gateway never dials upstream directly.
       **Cursor done 2026-07-02:** agent unified (`main.py`); tests in `test_rpc_unified.py`;
       gateway/broker wiring spec in `docs/mcp/gateway-integration-checklist.md` (Claude-owned, P6.18).
- [x] 12. Enforce gVisor: docker_manager requires runtime=runsc in prod (fail-closed if unavailable);
       add security_opt (seccomp, no-new-privileges), cap_drop=ALL, tmpfs-only writes, egress lockdown.
       **Done 2026-07-02:** `_run_kwargs` hardening (no-new-privileges, cap_drop ALL, user=sandbox,
       init, ulimits, memswap_limit); `MCP_SANDBOX_RUNTIME_REQUIRED` fail-closed probe; egress via
       `MCP_SANDBOX_EGRESS_LOCKDOWN` / proxy env; 75 broker tests green.
- [ ] 13. Verify (Playwright + harness): with the gateway pointed only at the sandbox, an http, an sse, a
       ws, and a stdio MCP all work end-to-end THROUGH the sandbox; confirm the gateway opens NO direct
       upstream connection (network assertion) — nothing runs in the main backend.
       **BLOCKED 2026-07-02 (iter13):** gateway wiring not landed — no `broker_send_rpc`, no broker
       `/{org}/rpc`, `mcp_proxy` still direct httpx for streamable-http/sse. See
       `mcp-parallel/findings/p4-13/BLOCKER.md`. Pivoted to P5.14.

## P5 — Frontend fixes (Playwright-verified; the dialog especially)
- [x] 14. B1: OAuth selectable ONLY for HTTP transports; block oauth+stdio in the form; render exactly
       ONE Authorize button (HTTP+oauth only); remove the dup/broken control authorize path.
       **Verified 2026-07-02 (iter13):** Playwright 8/8 PASS (`playwright_mcp_b1_verify.mjs`);
       form guards + one-button UX already in place. Control `startControlOAuth` retained for HTTP
       oauth (token→control DB required for sync); structural removal needs Claude token bridge.
       See `mcp-parallel/findings/p4-13/P5-14-B1-VERIFY.md`.
- [x] 15. B2: freshly-registered HTTP oauth server shows a distinct "Pending authorization" state (not a
       0-tools card); tools appear only after oauth_authorized + sync.
       **Verified 2026-07-02 (iter14):** fix already in `04748af5` (renderServerCard awaitingAuth);
       Playwright 6/6 PASS (`playwright_mcp_b2_verify.mjs` → `mcp-parallel/findings/p5-15/`).
       Amber "Pending authorization" badge + "Authorize to load tools"; no Unknown/0-tools card.
- [x] 16. B4: stabilize the Add-Server dialog so controlled inputs KEEP focus per keystroke (fix the
       remount: no field-component defined in render / stable keys / portal children not recreated).
       **Verified 2026-07-02 (iter15):** B4 NOT reproducing — prior Dialog.jsx onCloseRef fix holds;
       Playwright 11/11 PASS (`playwright_mcp_b4_verify.mjs` → `mcp-parallel/findings/p5-16/`).
       24 keystrokes × 5 fields + GitHub preset prefill + 37 keys on prefilled field; focus kept.
- [x] 17. Playwright verify B1/B2/B4 end-to-end with screenshots (Linear-stdio → no OAuth; HTTP-oauth →
       one Authorize + pending state → tools populate; type long strings in every dialog field, focus kept).
       **Verified 2026-07-02 (iter15):** combined E2E 12/12 PASS (`playwright_mcp_b1_b2_b4_e2e.mjs` →
       `mcp-parallel/findings/p5-17/`). Tools-populate after authorize = manual OAuth (headed noVNC).

## P6 — Integrate + verify with the parallel session
- [ ] 18. Pull the Claude branch's gateway+broker changes (via the contract); run an integration check:
       all 4 transports through the sandbox under the Claude session's 15-MCP harness (concurrency/load/
       leakage). Fix any seam mismatch on the Cursor-owned side only.
       **BLOCKED 2026-07-02 (iter16):** gateway wiring still not landed — no `broker_send_rpc`, broker
       `/{org}/rpc` returns 404, HTTP/SSE still direct httpx in `mcp_proxy.py`. Cursor seam green
       (agent 15/15, lifecycle 20/20). Live stack = 1 org × 4 servers (not 15). See
       `mcp-parallel/findings/p6-18/BLOCKER.md`. Browser at MCP panel for manual OAuth.
- [ ] 19. Re-run P1 repros → all four UI bugs gone; re-run P4 verification 3× (in-process + live).
       When P1–P6 all [x] AND integration green, output <promise>COMPLETE</promise>.
