# MCP Gateway Ralph Progress   (mark [x] when DONE + verified; never fake green)

## Codebase Patterns (append reusable learnings at top)
- gateway MCP: mcp_proxy / mcp_oauth(_proxy) / mcp_stdio_adapter / mcp_ws_adapter / mcp_sandbox_client
- broker: services/mcp-broker (docker_manager, registry, reaper, docker_health, routes; sandbox-image/agent/stdio_manager)
- control: control/ai_mesh_control/mcp_connector (serializers, mcp_firewall_client, resync command)
- frontend: MCPConnectorPanel(Inner) register/authorize/list/execute; Dialog = createPortal, returns null when closed
- INVARIANT: OAuth requires an HTTP URL; stdio has none → oauth+stdio is invalid, must be blocked in the form
- INVARIANT (leak): one sandbox per org; no cross-tenant tool/data/credential visibility; egress bytes are truth
- B1/B2 UX decision table (item#10, full in docs/mcp/oss-research-oauth-transport-ux.md): MCP has exactly 2 transports — stdio(subprocess/NO url/env-creds/NO oauth) + Streamable HTTP(single url endpoint/oauth applies); sse=deprecated HTTP variant. oauth is HTTP-only (RFC8707 resource=canonical HTTP URI, PRM/AS discovery is HTTP). RULES: oauth selectable ONLY for streamable-http|sse; BLOCK oauth on url-less stdio (that's B1's "block oauth+stdio") but stdio+mcp-remote is the LEGIT exception (row stays auth_type=none, gateway owns OAuth via serverNeedsOAuth→startOAuth). ≤1 Authorize button/card (gateway-cond vs http-oauth-cond are mutually exclusive). Fresh oauth server = PENDING badge, never a ready 0-tools card (B2); tools only after oauth_authorized+sync. B1 remaining defect = the CONTROL authorize path (views.py:2513) still reachable, emits 400 "Server has no URL" (:2526) + sets auth_type=oauth bypassing guard (:2582) → #13 must make it structurally unreachable (recommend: unify HTTP-oauth onto gateway oauth/start path). Industry norm (VS Code/Claude): "Needs Auth" status + ONE Authenticate btn that opens browser (guard the #42359 silent-no-op bug in #14).
- P7 hardening (item#9): _run_kwargs (docker_manager.py:247) is the only create-time knob. Cheap adds w/ ~0 compat risk: security_opt=['no-new-privileges:true'], cap_drop=['ALL'], ulimits=[Ulimit(nofile),Ulimit(nproc)], init=True, user='4000:4000'. memswap_limit=mem_limit closes swap-bypass. storage_opt={'size':..} ONLY on quota-capable FS (overlay2+xfs pquota) else it hard-errors create → gate on FS detect or reap-by-usage. runtime='runsc' (gVisor, hook already at :283) = biggest untrusted-code isolation win. EGRESS is the exfil channel: per-org bridge has full NAT (needed for npx) → needs host default-deny allowlist proxy (npm/PyPI + declared remote-MCP hosts only); exact-host match, no wildcards. Command allowlist (stdio_manager.py:37) ≠ package allowlist — broker path lacks _PACKAGE_ALLOWLIST that the in-proc path has (mcp_stdio_adapter.py:362). Details: docs/mcp/oss-research-docker-hardening.md

## P0 — Analysis
- [x] 1. Map gateway MCP request path (proxy→oauth→adapter→sandbox_client→broker) with file:line — docs/mcp/request-path-map.md (commit 4d970d1b)
- [x] 2. Map broker sandbox lifecycle (create→install→start stdio→health→reap) + resource limits — docs/mcp/broker-sandbox-lifecycle.md (commit fc268c58)
- [x] 3. Map control mcp_connector registration + oauth_authorized + tool-sync flow — docs/mcp/control-plane-flow.md (commit 8f952afd)
- [x] 4. Map frontend MCPConnectorPanel register/authorize/list/execute + the 4 bug sites — docs/mcp/frontend-panel-flow.md (commit 85a3c6e2)
- [x] 5. Write docs/mcp/ARCHITECTURE_AND_THREATS.md (isolation model + threat model + the 4 bugs) — docs/mcp/ARCHITECTURE_AND_THREATS.md (commit 0b2b17c2)

## P1 — OSS exploration (GitHub MCP)
- [x] 6. Study modelcontextprotocol/servers stdio server patterns (how official stdio servers start/handshake) — docs/mcp/oss-research-stdio-patterns.md (commit 0c5d877f)
- [x] 7. Study MCP auth spec + OAuth 2.1 (PKCE, RFC 9728 protected-resource + RFC 8414 AS metadata discovery) — docs/mcp/oss-research-oauth-spec.md (commit 52145d81)
- [x] 8. Study mcp-remote (how it wraps a remote HTTP MCP over stdio + does OAuth) — clarifies Linear's stdio+remote case — docs/mcp/oss-research-mcp-remote.md
- [x] 9. Study Docker sandboxing hardening (seccomp/gVisor/read-only rootfs/no-new-privileges/limits) + multi-tenant patterns — docs/mcp/oss-research-docker-hardening.md

## P2 — Deep research (web)
- [x] 10. OAuth-for-MCP + transport rules: why oauth needs an HTTP URL; correct UX for stdio-wrapped-remote — docs/mcp/oss-research-oauth-transport-ux.md (B1/B2 decision table)
- [ ] 11. Docker multi-tenant isolation hardening checklist for running untrusted npm packages
- [ ] 12. Concurrency/load patterns for sandboxed subprocess MCP servers (pooling, warm start, backpressure)

## P3 — Fix B1 (oauth+stdio / 2 buttons / no URL)
- [ ] 13. Form: OAuth selectable ONLY for HTTP transports; block oauth+stdio; render exactly one Authorize button (HTTP+oauth only); remove the dup/broken control authorize path
- [ ] 14. Playwright verify: register Linear(stdio) → NO OAuth button, no "no URL" error; register an HTTP oauth server → exactly one Authorize, popup opens

## P4 — Fix B2 (lists 0-tools before oauth)
- [ ] 15. HTTP oauth server on register shows a distinct "Pending authorization" state (or triggers OAuth inline); NOT a normal 0-tools card; tools appear only after oauth_authorized + sync
- [ ] 16. Playwright verify: register → pending state → authorize → tools populate

## P5 — Fix B4 (modal focus loss)
- [ ] 17. Stabilize the Add Server modal/form subtree (define fields outside render / stable keys / no portal-children recreation) so focus persists per keystroke
- [ ] 18. Playwright verify: type a long string into each field without losing focus; presets prefill correctly

## P6 — Fix B3 (sandbox temporarily unavailable)
- [ ] 19. Eager sandbox provisioning on register/authorize/first-sync (warm the per-org sandbox) 
- [ ] 20. Readiness poll + bounded backoff retry in mcp_sandbox_client; broker returns clear "provisioning" not just 503
- [ ] 21. Verify: first tool call after register succeeds or shows a clear provisioning state (no hard "temporarily unavailable")

## P7 — Sandbox isolation hardening (the dev-plan)
- [ ] 22. Enforce resource limits per sandbox (CPU, memory, disk, timeout) + verify a runaway MCP is contained
- [ ] 23. Confirm NO unknown npm package executes on the host — only inside the per-org sandbox
- [ ] 24. Reaper/idle cleanup correctness (no orphan sandboxes; restart-safe registry) 
- [ ] 25. Per-org credential/env isolation (Org A env/secrets never visible in Org B sandbox)

## P8 — 15-MCP parallel harness
- [ ] 26. Provision 3 orgs × 5 servers (§1.4); extend scripts/mcp_live_matrix_harness.py / mcp_pipeline_matrix_live.py
- [ ] 27. Harness drives parallel tool calls across all 15 via the gateway (OpenAI/MCP path), capturing egress + audit per call

## P9 — Concurrency / load / leakage
- [ ] 28. Concurrency: fire parallel tool calls across all 15 (Everything.echo/add deterministic) — assert correct isolation + no dropped/mixed responses
- [ ] 29. Load: sustained calls — sandbox reuse/pooling holds, no exhaustion, no 503 storms, resource limits respected
- [ ] 30. Cross-tenant leakage: Org A cannot list/call Org B's servers, cannot read B's tool results/creds; plant a canary in Org B (Everything/Filesystem) and prove Org A never observes it on any channel
- [ ] 31. OAuth/transport correctness across the fleet under load (no stdio server ever attempts OAuth; HTTP oauth servers authorize cleanly)

## P10 — Recursive verification
- [ ] 32. Re-run P3–P9 end-to-end (in-process + live); adversarial pass; all green 3× → emit COMPLETE