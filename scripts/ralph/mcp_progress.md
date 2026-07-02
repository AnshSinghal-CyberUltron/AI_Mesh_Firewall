# MCP Gateway Ralph Progress   (mark [x] when DONE + verified; never fake green)

## Codebase Patterns (append reusable learnings at top)
- gateway MCP: mcp_proxy / mcp_oauth(_proxy) / mcp_stdio_adapter / mcp_ws_adapter / mcp_sandbox_client
- broker: services/mcp-broker (docker_manager, registry, reaper, docker_health, routes; sandbox-image/agent/stdio_manager)
- control: control/ai_mesh_control/mcp_connector (serializers, mcp_firewall_client, resync command)
- frontend: MCPConnectorPanel(Inner) register/authorize/list/execute; Dialog = createPortal, returns null when closed
- INVARIANT: OAuth requires an HTTP URL; stdio has none → oauth+stdio is invalid, must be blocked in the form
- INVARIANT (leak): one sandbox per org; no cross-tenant tool/data/credential visibility; egress bytes are truth
- REQUEST PATH (item#1, docs/mcp/request-path-map.md): main.py mounts 4 routers (:4023-4041); spine=org_mcp_jsonrpc
  (mcp_proxy.py:1848) auth(_validate_org_scope:1562 401/403 org-isolation)→ratelimit(:1888)→config/transport
  (:1897-1899, default streamable-http)→initialize LOCAL(:1902)→per-tool/key policy→arg-scan+cred-block(:2121)→
  DISPATCH _adapter_forward:1775 (stdio:1788 / ws:1809 / broad-except:1832 'Adapter error') OR backend-HTTP(:2324)
  →result-scan+redaction-floor→audit _record_gateway_event:425. stdio→send_jsonrpc(mcp_stdio_adapter.py:666)→
  broker(:602) vs in-process(:628) on _stdio_in_process(:678).
- B1 NUANCE (must respect in item#13): a 'stdio' server CAN legitimately have a URL — mcp-remote is a stdio proc
  wrapping a REMOTE HTTP MCP URL and DOES OAuth (_maybe_inject_oauth_header:1732 scans args for mcp-remote+URL).
  So 'block oauth+stdio' == block oauth+PURE-local-stdio(no URL), NOT oauth+mcp-remote. String 'Server has no URL'
  is ABSENT from all backend files (grep-confirmed) → it's frontend-only. oauth/start only requires non-empty
  SSRF-safe server_url (mcp_oauth_proxy.py:444-455), no transport check.
- B2 SIGNAL: distinguish 'never authed' vs 'authed-but-empty' via mcp_oauth_proxy.oauth_status:709 / has_stored_token:259;
  spine tools/list returns {tools:[]} with NO oauth-needed signal (mcp_proxy.py:1979-1986) + initialize always ok(:1902).
- B3 ROOT: 'MCP sandbox is temporarily unavailable' = mcp_sandbox_client.py:58 (broker 502) & :100 (unreachable after
  RETRY_MAX=5). Causes: broker_send_jsonrpc:136 never ensure_sandbox first (no eager provision); _request_with_503_retry:104
  retries ONLY 503 (502 not retried); broker readiness=='running' not agent-bound; broker collapses agent HTTP>=400 to
  502 (routes.py:189-198). Fix=items 19-21.
- BROKER LIFECYCLE (item#2, docs/mcp/broker-sandbox-lifecycle.md): routes.stdio_rpc:161 (docker gate cached_docker_ok
  docker_health.py:22; quota _check_org_quota:66 MAX_ORGS=50; _resolve_running_sandbox:93->DockerManager.ensure:345->
  create_container:300/_run_kwargs:247->_sync_registry:175; _post_agent_rpc:106 8-retry backoff-> in-container agent
  /rpc:54->send_jsonrpc:411->_ensure_process:194 spawn npx/uvx:256->_ensure_initialized:369). Limits: mem 2048m(:269)/
  cpu 1.0(:270)/pids 256(:271)/tmpfs /tmp noexec+npm-cache exec 1g(:274-276). Two-level reaper: broker reaper.py:31
  (idle containers, stop not destroy) + agent stdio_manager _reaper_loop:486 (idle/hung children).
- P7 HARDENING GAPS (confirmed ABSENT both broker & image, fix #22-23): NO security_opt=no-new-privileges, NO
  cap_drop=ALL, NO seccomp/apparmor, NO storage_opt/volume size cap (->/data/mcp-auth grows unbounded), NO ulimits
  (only pids), NO run-enforced user= (relies on image USER sandbox), gVisor only if MCP_SANDBOX_RUNTIME set (default
  runc), NO egress allowlist, agent /rpc UNAUTHENTICATED (main.py:54, relies on per-org net), NO npm/PyPI PACKAGE
  allowlist on broker path (cf gateway in-process has _PACKAGE_ALLOWLIST mcp_stdio_adapter.py:362-371), NO tini/init.
  Edge: node -e/python -c = RCE (containment=sandbox); org_slug sanitize divergence container='-' vs volume='_'
  (docker_manager.py:87) collision risk; host-run broker branch(:280-282) collapses per-org net to shared bridge.
- ITEM#24 RESTART-SAFETY GAPS: registry IN-MEMORY only (registry.py:23, empty on restart); NO boot label-reconciliation;
  orphan containers NEVER reaped (reaper iterates only registry.idle_entries:41); reaper has NO try/except (reaper.py:54)
  -> one docker APIError kills reaping permanently (agent reaper IS re-armed :504 — asymmetry); quota bypass post-restart +
  TOCTOU (routes.py:66/155). reap uses stop not destroy (containers+volumes accumulate).
- ITEM#25 CRED/ENV ISOLATION (already STRONG): shared/ai_mesh_shared/mcp_stdio_common.py _build_child_env:119 —
  _SECRET_ENV_DENYLIST:19 strips broker/gateway keys+DB/REDIS/AWS+PYTHONPATH; env passthrough is ALLOWLIST(:81);
  LD_PRELOAD/DYLD_INSERT_LIBRARIES stripped(:140); per-org MCP_REMOTE_CONFIG_DIR pinned(:144).
- B3 (broker side): ensure returns on container 'running'(container_status:132) NOT agent-bound; warm flag IGNORED
  (routes.py:153); agent /health(main.py:43) only means uvicorn bound not MCP-initialized(:369); agent_url can be
  127.0.0.1 right after create(docker_manager.py:140). Fix #19-21: eager provision+readiness poll; broker return
  'provisioning' 503 not collapse-to-502; client backoff distinguishes provisioning from hard failure.
- DEFENSE-IN-DEPTH (do not weaken): stdio command allowlist+no-shell (mcp_stdio_adapter.py:342/350/407), package
  allowlist/pinning(:362-371), per-org+global proc caps(:389-401), sandboxed child env pins per-org MCP_REMOTE_CONFIG_DIR(:403),
  WS SSRF guard(mcp_ws_adapter.py:124), broker fail-closed key auth(auth.py:17-23)+per-org quota(routes.py:66),
  ext proxy egress allowlist(mcp_proxy.py:97/939), OAuth PKCE-S256(mcp_oauth.py:527)+SSRF re-guard on derived endpoints.

## P0 — Analysis
- [x] 1. Map gateway MCP request path (proxy→oauth→adapter→sandbox_client→broker) with file:line
      EVIDENCE: docs/mcp/request-path-map.md (spine=mcp_proxy.py org_mcp_jsonrpc:1848; transport@:1898-1899;
      dispatch _adapter_forward:1775→stdio_adapter.send_jsonrpc:666→broker_send_jsonrpc(mcp_sandbox_client.py:136)
      →broker stdio_rpc(routes.py:161); OAuth self-AS mcp_oauth.py + per-org proxy mcp_oauth_proxy.py inject@:1732).
      All anchors spot-verified against tree (never faked). B1/B2/B3 sites located; B4 confirmed frontend-only.
- [x] 2. Map broker sandbox lifecycle (create→install→start stdio→health→reap) + resource limits
      EVIDENCE: docs/mcp/broker-sandbox-lifecycle.md (create=docker_manager.ensure:345/_run_kwargs:247;
      install=lazy npx/uvx INSIDE container stdio_manager.py:256; start=_ensure_process:194; health=/health
      main.py:43 (bound only) vs real readiness _ensure_initialized:369; reap=reaper.py:31 + agent _reaper_loop:486).
      LIMITS: mem 2048m/cpu 1.0/pids 256/tmpfs; ABSENT (P7 #22-23): security_opt(no-new-priv), cap_drop, seccomp,
      storage_opt/vol-quota, ulimits, run-enforced user=, pkg allowlist(broker path), tini/init. All anchors verified.
- [ ] 3. Map control mcp_connector registration + oauth_authorized + tool-sync flow
- [ ] 4. Map frontend MCPConnectorPanel register/authorize/list/execute + the 4 bug sites
- [ ] 5. Write docs/mcp/ARCHITECTURE_AND_THREATS.md (isolation model + threat model + the 4 bugs)

## P1 — OSS exploration (GitHub MCP)
- [ ] 6. Study modelcontextprotocol/servers stdio server patterns (how official stdio servers start/handshake)
- [ ] 7. Study MCP auth spec + OAuth 2.1 (PKCE, RFC 9728 protected-resource + RFC 8414 AS metadata discovery)
- [ ] 8. Study mcp-remote (how it wraps a remote HTTP MCP over stdio + does OAuth) — clarifies Linear's stdio+remote case
- [ ] 9. Study Docker sandboxing hardening (seccomp/gVisor/read-only rootfs/no-new-privileges/limits) + multi-tenant patterns

## P2 — Deep research (web)
- [ ] 10. OAuth-for-MCP + transport rules: why oauth needs an HTTP URL; correct UX for stdio-wrapped-remote
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