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
- ⚠️ PRIOR-FIXES-PRESENT (item#4, docs/mcp/frontend-panel-flow.md): B1/B2/B4 are NOT greenfield — the frontend
  already has substantial fixes (comments cite 'MCP OAuth bugs #1/#2', 'bug #3', 'FOCUS-LOSS ROOT FIX'; deleted
  iter1-bug5-modal-focus-fixed.png). TREAT FIX ITEMS #13-18 AS VERIFICATION-FIRST, not blind re-implementation.
  B1 form: MCPConnectorPanel.jsx oauth auth_type option filtered to streamable-http/sse only (:1483-1490) + transport
  switch drops oauth->none (:1385-1392); cards render EXACTLY ONE Authorize (mutually-exclusive serverNeedsOAuth:711->
  startOAuth:773 [gateway, stdio mcp-remote] vs serverUsesHttpOAuth:726->startControlOAuth:845 [control, http-oauth+url]);
  serverUsesHttpOAuth requires !!srv.url so 'Server has no URL' unreachable from button. REMAINING B1(#13): backend
  MCPServerOAuthStartView dup path (views.py:2513/2526/2582) + startControlOAuth caller still exist — spec wants them
  deleted/unified to gateway path. B2: serverAwaitingAuth:741 renders 'authorization required' badge (:1095-1097),
  syncBlockedForAuth:757 gates premature sync (:1211). REMAINING B2(#15): in-browser verify distinct pending UX + tools
  populate after authorize+sync. B4: NO component-in-render (render helpers are fn CALLS renderServers()/renderTools()
  @ :2203-2208, NOT <Comp/>); Dialog focus fix in ui/Dialog.jsx (onClose kept in ref :20-24, handleKey useCallback([]),
  effect deps [open,handleKey] :69 -> effect runs only on open-toggle not per keystroke). REMAINING B4(#17/18): Playwright
  confirm focus retained per keystroke (code-level root causes already addressed). Modal=Dialog(createPortal) @ :1347.
- CONTROL FLOW (item#3, docs/mcp/control-plane-flow.md): register POST /api/mcp-connector/servers/ (MCPServerListCreateView.post
  views.py:764) -> serializer.validate (serializers.py:121, ALL guards incl B1@177) -> get_or_create(:784) -> auto-provision
  GatewayAPIKey(:821) -> 201 tools_count=0. NO tool sync, NO sandbox provision on register (B2/B3). post_save signal only
  bump_scan_version (signals.py:154, Redis INCR; token/oauth saves EXCLUDED via _SERVER_RELEVANT_FIELDS:167). oauth_authorized=
  models.py:178 property (auth_type=='oauth' and bool(auth_token)). OAuth: MCPServerOAuthStartView.post(views.py:2513) discover
  (oauth.py:169)+DCR(:237)+PKCE -> callback MCPOAuthCallbackView.get(:2658, matched by oauth_state:2672) -> exchange_code(oauth.py:321)
  -> _store_oauth_tokens(views.py:298/2697) sets auth_token -> oauth_authorized True (DOES NOT auto-sync). TOOL SYNC only via
  MCPServerToolListView.post(:1889/1906)->_resync_server_tools(:507)->_discover_tools_via_gateway(:407, gateway /v1/mcp/internal/
  discover-tools:465); fresh unauth oauth -> _ensure_oauth_token_fresh(:331) False -> ([],reauth err):448/452 -> 0 tools+
  connection_status='failed'+needs_reauth. mgmt cmd resync_mcp_servers also calls _resync_server_tools. tasks.py:12 only audit (no resync task).
- B1 BACKEND STATUS (item#13): registration guard EXISTS+CORRECT (serializers.py:177 rejects oauth unless streamable-http/sse,
  test_oauth_transport_guard.py). BUT MCPServerOAuthStartView.post(views.py:2513) sets server.auth_type='oauth' DIRECTLY at :2582
  bypassing that guard (only checks server.url:2524 -> else 400 'Server has no URL':2526). This control authorize path (urls.py:19
  servers/{pk}/oauth/authorize/ + callback :20) is REDUNDANT with gateway mcp_oauth_proxy. B1 fix: FE one Authorize button (gateway
  path) for HTTP+oauth only; REMOVE/guard MCPServerOAuthStartView so :2526 unreachable. FE dup flagged MCPConnectorPanel.jsx:722.
- B2 BACKEND STATUS (item#15): already exposes right signals — oauth_authorized(models.py:178), needs_reauth, connection_status.
  Fresh oauth server = auth_token empty -> oauth_authorized False -> 0 tools until authorize+manual sync. FE fix: render distinct
  'Pending authorization' card when auth_type=='oauth' && !oauth_authorized (never normal 0-tools card); ideally auto-resync after callback.
- B3 CONTROL STATUS: neither register nor authorize provisions sandbox; gateway provisions lazily on first discover/tools-call.
  Fix#19: control->gateway eager ensure on register/authorize/first-sync.
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

- OAUTH SPEC COMPLIANCE (item#7, docs/mcp/oss-research-oauth-spec.md): MCP Authorization spec 2025-06-18 MANDATES the
  B1 invariant — 'Implementations using an STDIO transport SHOULD NOT follow this specification, and instead retrieve
  credentials from the environment'; HTTP transports SHOULD conform. OAuth discovery (RFC9728 PRM via 401 WWW-Authenticate
  -> RFC8414 AS metadata) + RFC8707 resource param all require an HTTP(S) server URL, so oauth+pure-stdio is spec-invalid
  (mcp-remote wraps a remote HTTP URL over stdio = the exception). Repo OAuth CLIENT is spec-compliant: control oauth.py
  discover:169/register_client:237/generate_pkce:48 (S256 @:284)/canonical_resource:67; resource param on authorize
  (:286) + exchange (:337) + refresh (:355) — RFC8707 MUST, all 3 verified; per-call SSRF re-guard on token_endpoint
  (:309) + follow_redirects=False (:312). gateway self-AS mcp_oauth.py (PRM:99/AS:140/DCR:167/token:464 returns API key
  as access_token:542/PKCE verify:527/redirect allowlist:80). TOKEN-PASSTHROUGH FORBIDDEN by spec -> repo uses a SEPARATE
  per-org upstream token (_token_save org|server_url, injected via _maybe_inject_oauth_header:1759), never forwards the
  inbound gateway token => confused-deputy safe (keep this). #31 must assert: no stdio ever does OAuth; http-oauth
  discovery+DCR+PKCE+resource clean; refresh works; expired->needs_reauth. SSRF re-guard EXCEEDS spec — do not weaken.
- OSS STDIO PATTERNS (item#6, docs/mcp/oss-research-stdio-patterns.md): official stdio MCP servers launch
  `npx -y @modelcontextprotocol/server-<name> [stdio]` (stdio default); handshake = initialize(protocolVersion
  "2024-11-05", capabilities) -> server result -> notifications/initialized. INVARIANT: stdout = JSON-RPC ONLY,
  logs to stderr, NO ready banner -> readiness can only be detected by completing an initialize probe (repo does
  this in _ensure_initialized gateway mcp_stdio_adapter.py:548 / agent stdio_manager.py:369). Cold-start latency =
  npx package FETCH (network+disk) => the dominant B3 first-call delay; mitigate via npm-cache tmpfs warm +
  eager-provision + initialize-probe readiness poll (B3 #19-21).
- ⚠️ P8/P9 HARNESS GOTCHA (items#26-31): Everything server sum tool was RENAMED add->get-sum (name is
  VERSION-DEPENDENT: older npm='add', current main='get-sum', both schema {a:number,b:number}). echo is stable
  (echo{message}->"Echo: <message>"). Harness must tools/list and pick the sum tool by name-in{add,get-sum}/schema
  OR pin @modelcontextprotocol/server-everything@<ver>. Do NOT hardcode 'add'. Filesystem canary (item#30):
  npx -y @modelcontextprotocol/server-filesystem <dir> (needs >=1 allowed dir); write canary in Org B dir, prove
  Org A read_text_file/list_directory/search_files never sees it (3 iso layers: allowed-dir + per-org sandbox+vol + gateway org-scope).

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
- [x] 3. Map control mcp_connector registration + oauth_authorized + tool-sync flow
      EVIDENCE: docs/mcp/control-plane-flow.md (register=MCPServerListCreateView.post views.py:764 no-sync/no-provision;
      B1 guard EXISTS serializers.py:177 but MCPServerOAuthStartView.post:2582 BYPASSES it (sets auth_type=oauth, only
      checks url:2524->'Server has no URL':2526); oauth_authorized=models.py:178 property flips on _store_oauth_tokens:298/2697;
      tools appear ONLY via MCPServerToolListView.post:1889->_resync_server_tools:507->_discover_tools_via_gateway:407,
      NOT on register/callback/signal). All anchors verified.
- [x] 4. Map frontend MCPConnectorPanel register/authorize/list/execute + the 4 bug sites
      EVIDENCE: docs/mcp/frontend-panel-flow.md. CRITICAL: B1/B2/B4 all carry SUBSTANTIAL PRIOR FIXES already
      in-code. B1 form: oauth option filtered to HTTP only (:1483-1490) + drops oauth on transport switch (:1385);
      cards render mutually-exclusive single Authorize (serverNeedsOAuth:711->startOAuth:773 gateway vs
      serverUsesHttpOAuth:726->startControlOAuth:845 control). B2: 'authorization required' pending badge renders
      (serverAwaitingAuth:741 @ :1095). B4: no in-render component (render helpers are fn calls :2203-2208) + Dialog
      focus-trap stabilized (ui/Dialog.jsx onClose-ref, deps [open,handleKey]). REMAINING: B1 delete backend dup
      MCPServerOAuthStartView path; B2/B4 in-browser verify. All anchors verified.
- [x] 5. Write docs/mcp/ARCHITECTURE_AND_THREATS.md (isolation model + threat model + the 4 bugs)
      EVIDENCE: docs/mcp/ARCHITECTURE_AND_THREATS.md (capstone) — consolidates items 1-4 maps: architecture (2 planes,
      request lifecycle, register/oauth/tool-sync), isolation model (invariants+present controls table + P7 GAPS),
      STRIDE threat model (T1 leakage..T8 audit, 6 trust boundaries), and B1-B4 root-cause/current-state/remaining.
      Also docs/mcp/AGENTS.md index. P0 ANALYSIS (items 1-5) COMPLETE.

## P1 — OSS exploration (GitHub MCP)
- [x] 6. Study modelcontextprotocol/servers stdio server patterns (how official stdio servers start/handshake)
      EVIDENCE: docs/mcp/oss-research-stdio-patterns.md (fetched real files via GitHub MCP from modelcontextprotocol/
      servers main). Launch npx -y @modelcontextprotocol/server-<name> [stdio]; handshake=initialize(protocolVersion
      2024-11-05)+notifications/initialized; stdout=JSON-RPC ONLY (logs->stderr), NO ready banner => readiness=answer
      initialize; cold-start latency=npx package fetch (B3 root). Everything echo{message}->"Echo: msg" (stable); sum
      tool RENAMED add->get-sum{a,b:number} (version-dependent!). Filesystem npx -y @mcp/server-filesystem <dir> (needs
      >=1 allowed dir) for P9 canary.
- [x] 7. Study MCP auth spec + OAuth 2.1 (PKCE, RFC 9728 protected-resource + RFC 8414 AS metadata discovery)
      EVIDENCE: docs/mcp/oss-research-oauth-spec.md (MCP Authorization spec 2025-06-18 fetched + RFCs). Spec MANDATES
      B1: 'STDIO SHOULD NOT follow this spec — retrieve creds from environment'; OAuth is HTTP-transport only (PRM/AS
      discovery + resource param need an HTTP URL). Repo OAuth client is SPEC-COMPLIANT: PRM discovery (oauth.py:169/
      mcp_oauth_proxy:283), RFC8414 AS meta, DCR:237, PKCE S256:284, resource param on authorize:286+exchange:337+
      refresh:355 (VERIFIED all 3, RFC8707 MUST), redirect allowlist mcp_oauth.py:80, SSRF re-guard+no-redirect:309.
      Token-passthrough FORBIDDEN -> repo uses SEPARATE per-org upstream token (never forwards inbound) = confused-deputy safe.
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