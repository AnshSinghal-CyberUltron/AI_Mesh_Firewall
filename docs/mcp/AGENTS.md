# docs/mcp/ — MCP gateway subsystem maps (agent index)

Verified file:line maps of the multi-tenant MCP gateway. Read the capstone first, then drill into the
subsystem map you need. Every anchor was read from the tree and spot-verified when written (commits
`4d970d1b`..`85a3c6e2`); re-verify a specific line before relying on it — code moves.

- **`ARCHITECTURE_AND_THREATS.md`** — START HERE. Architecture (2 planes, request lifecycle,
  register→oauth→tool-sync), isolation model (invariants + present controls + P7 gaps), STRIDE threat
  model (T1 cross-tenant leakage … T8 audit; 6 trust boundaries), and the 4 bugs
  (root cause / current state / remaining).
- `request-path-map.md` — gateway MCP request path: `mcp_proxy.py` spine (`org_mcp_jsonrpc:1848`),
  `_adapter_forward:1775`, stdio/ws adapters, `mcp_sandbox_client`, OAuth (`mcp_oauth*.py`).
- `broker-sandbox-lifecycle.md` — `services/mcp-broker/`: create→install→start→health→reap, resource
  limits, per-org isolation, and the confirmed P7 hardening gaps.
- `control-plane-flow.md` — `control/.../mcp_connector/`: registration + `oauth_authorized` + tool-sync;
  the B1 registration guard (`serializers.py:177`) and the B1 bypass (`views.py:2582`).
- `frontend-panel-flow.md` — `MCPConnectorPanel.jsx`: register/authorize/list/execute + B1/B2/B4 bug
  sites and their **substantial prior fixes** (fix items are verification-first).
- `oss-research-oauth-spec.md` — MCP Authorization spec 2025-06-18 + RFCs (9728/8414/7591/8707/OAuth2.1):
  compliance matrix for the repo's OAuth client (spec-compliant, resource param verified on
  authorize+token+refresh) and the spec-mandated B1 invariant ("STDIO SHOULD NOT follow this spec").
- `oss-research-stdio-patterns.md` — official MCP stdio server start/handshake (`modelcontextprotocol/
  servers`): npx launch, initialize handshake, stdout=JSON-RPC-only/no-ready-banner (→B3 readiness =
  initialize probe; cold-start = npx fetch), Everything echo/`get-sum`(≠`add`, version-dependent) +
  Filesystem canary for P8/P9.
- `oss-research-mcp-remote.md` — `geelen/mcp-remote` stdio↔remote-HTTP bridge + OAuth: why a `stdio` row
  legitimately carries an HTTP URL (Linear case) and does gateway-side OAuth with `auth_type="none"`;
  the `--header` Bearer pre-injection that keeps mcp-remote headless (`mcp_proxy.py:1732/1759`); per-org
  `MCP_REMOTE_CONFIG_DIR` token isolation. **B1 nuance: "block oauth+stdio" = block `auth_type="oauth"`
  on a URL-less local stdio row, NOT block stdio-via-mcp-remote.**
- `oss-research-oauth-transport-ux.md` — **the B1/B2 (transport × auth_type) UX decision table** the fix
  items #13–16 implement/verify against. MCP has exactly 2 transports (stdio=no-url/no-oauth/env-creds;
  Streamable HTTP=url/oauth; sse=deprecated HTTP). oauth selectable ONLY for streamable-http/sse; stdio+
  mcp-remote is the legit gateway-owned-OAuth exception. ≤1 Authorize button; fresh oauth = pending badge
  not 0-tools card. B1 remaining = delete/hard-gate the reachable control authorize path (views.py:2513
  "Server has no URL" + guard-bypass); recommend unifying HTTP-oauth onto the gateway path.
- `oss-research-concurrency-load.md` — pooling/warm-start/backpressure patterns mapped to the repo's mature
  concurrency model (pool+reuse, request-id demux, caps 20/16/4). **Contains the B3 ROOT CAUSE**: broker
  returns 502 for cold-start agent-connection errors but the client retries only 503 → first-call-after-
  register surfaces "temporarily unavailable". Fix = eager warm (honor ignored `warm` flag) + broker 503-
  provisioning+Retry-After (not 502) + client readiness poll. P8/P9 sizing: 15 MCPs fit under caps; pre-warm
  to skip the `MAX_CONCURRENT_INITS=4` bottleneck; no-503-storm = Retry-After + per-sandbox circuit breaker.
- `oss-research-npm-untrusted-checklist.md` — the npm/Node **supply-chain** layer (N1–N7) for P7 #22–23:
  `npx`/`uvx` runs postinstall RCE + fetches unpinned-latest before the server starts (Shai-Hulud vector).
  Verified path asymmetry (gateway path has allowlist+pin default-OFF; broker path has neither; neither sets
  `npm_config_ignore_scripts`/registry). Layer-A controls sit on top of item-#9's Layer-B container
  containment. P7 acceptance = malicious-postinstall fixture proven inert via the #30 egress capture.
- `oss-research-docker-hardening.md` — sourced Docker multi-tenant hardening checklist (OWASP/gVisor/
  Docker AI-sandbox/iron-proxy) mapped to `docker_manager.py:_run_kwargs`: H1–H15 controls each with the
  exact `docker-py` kwarg, present/absent-in-repo, and the P7 item (#22–25) that implements it. Priority
  order + the egress default-deny-proxy pattern for #30 leakage proof. Companion to
  `broker-sandbox-lifecycle.md` §2 gap table.

Nearby subsystem AGENTS.md: `services/mcp-broker/AGENTS.md`,
`control/ai_mesh_control/mcp_connector/AGENTS.md`, `frontend/AGENTS.md` (MCP panels section).
Live progress + learnings: `scripts/ralph/mcp_progress.md`. Ruflo memory namespace: `mcp-gateway`.
