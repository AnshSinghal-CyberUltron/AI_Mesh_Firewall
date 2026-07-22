# Multi-Tenant MCP Gateway — Architecture & Threat Model

> **P0 item #5** capstone for `scripts/ralph/mcp_progress.md`. Consolidates the four verified
> subsystem maps into one architecture + isolation + threat model, and states each of the four
> confirmed bugs with root cause / current state / remaining work.
>
> Source maps (every file:line below traces to one of these, all spot-verified):
> - `docs/mcp/request-path-map.md` — gateway MCP request path (proxy→oauth→adapter→sandbox_client→broker)
> - `docs/mcp/broker-sandbox-lifecycle.md` — broker sandbox lifecycle + resource limits + P7 gaps
> - `docs/mcp/control-plane-flow.md` — control registration + oauth_authorized + tool-sync
> - `docs/mcp/frontend-panel-flow.md` — MCPConnectorPanel + the 4 bug sites

---

## 1. Architecture

```
 ┌──────────┐  JWT   ┌───────────────────┐  X-Gateway-Internal-Key   ┌──────────────────┐
 │ Frontend │───────▶│  Control plane    │◀─────────internal────────▶│   Gateway        │
 │ (React,  │  /api  │  (Django DRF)     │   /v1/mcp/internal/*       │  (FastAPI)       │
 │ MCP panel│        │  registry, OAuth  │                            │  proxy + policy  │
 └──────────┘        │  client, policy,  │                            │  + scan + audit  │
                     │  audit sink       │                            └───────┬──────────┘
                     └───────────────────┘                                    │ X-MCP-Broker-Key
                          │ Redis (scan-version, tokens)                       ▼
                          │                                          ┌──────────────────┐
                          │                                          │  mcp-broker      │
                          │                                          │  Sandbox Ctrl    │
                          │                                          └───────┬──────────┘
                          │                                one Docker container per ORG (per-org net+vol)
                          │                                                  ▼
                          │                                  ┌──────────────────────────────┐
                          │                                  │  in-container agent (PID1)    │
                          │                                  │  /rpc /health, spawns stdio   │
                          │                                  │  npx/uvx MCP child (untrusted)│
                          │                                  └──────────────────────────────┘
 HTTP-transport MCP servers (streamable-http/sse) are proxied by the gateway DIRECTLY to the upstream URL
 (no sandbox); only stdio MCP servers run inside the per-org broker sandbox.
```

### Two planes
- **Control plane** (`control/ai_mesh_control/mcp_connector/`): server registry (source of truth),
  registration validation, OAuth 2.1 *client* for HTTP servers, per-tool policy, audit sink. Never
  executes MCP tools. → `control-plane-flow.md`.
- **Data plane** (`gateway/ai_mesh_gateway/` + `services/mcp-broker/`): the gateway authenticates,
  authorizes, rate-limits, scans, and audits *every* MCP request, then dispatches — HTTP transports
  directly to the upstream URL, stdio transports into the per-org broker sandbox. → `request-path-map.md`,
  `broker-sandbox-lifecycle.md`.

### Request lifecycle (spine)
`org_mcp_jsonrpc` (`mcp_proxy.py:1848`): org-scope auth (`:1855`) → rate limit (`:1888`) → resolve
transport (`:1897-1899`) → per-key/per-tool policy (`:2007-2111`) → inbound arg scan + credential
hard-block (`:2121`) → **dispatch** (`_adapter_forward:1775` for stdio/ws, or backend HTTP `:2324`) →
outbound result scan + redaction floor → audit (`_record_gateway_event:425`). stdio →
`mcp_stdio_adapter.send_jsonrpc:666` → broker (`_send_jsonrpc_broker:591`, `broker_send_jsonrpc` in
`mcp_sandbox_client.py:136`) → broker `stdio_rpc` (`routes.py:161`) → in-container agent `/rpc` →
`stdio_manager.send_jsonrpc:411`.

### Registration → authorize → tool-sync (control)
`POST /servers/` (`views.py:764`) validates (`serializers.py:121`) + creates the row (no sync, no
sandbox provision). For HTTP oauth: authorize (`MCPServerOAuthStartView:2513`) → callback
(`:2658`) → `_store_oauth_tokens:298` sets `auth_token` → `oauth_authorized` (`models.py:178`) flips
True. Tools appear only via explicit `POST /servers/{pk}/tools/` (`:1889` → `_resync_server_tools:507`
→ gateway `/v1/mcp/internal/discover-tools`).

---

## 2. Isolation model (defense-in-depth) — invariants & current controls

| Invariant | Control (present) | file:line |
|---|---|---|
| **One sandbox per org** | per-org Docker network `mcp_sandbox_net_{org}` + per-org named volume `mcp_sandbox_{org}_auth` + one container | `docker_manager.py:225/267/268` |
| **No unknown npm on host** | npx/uvx fetch+run INSIDE the container only; `shell=False` argv exec; command allowlist (npx,node,python,python3,uvx,uv); path-qualified commands rejected | `stdio_manager.py:256/37/202`; gateway in-process path also `mcp_stdio_adapter.py:342/350` |
| **Per-sandbox CPU/mem/pids/timeout** | `mem_limit=2048m`, `nano_cpus=1.0`, `pids_limit=256`, read-only rootfs, sized tmpfs; agent RPC timeout 130s; init 120s | `docker_manager.py:269-276`, `routes.py:19` |
| **Reaper cleanup** | container reaper idle>600s (`reaper.py`) + in-container agent reaper idle/hung children | `reaper.py:31`, `stdio_manager.py:486` |
| **Per-org credential/env isolation** | `_build_child_env` secret denylist, allowlist passthrough, `LD_PRELOAD`/`DYLD_*` strip, per-org `MCP_REMOTE_CONFIG_DIR` pin; secrets Fernet-encrypted at rest | `mcp_stdio_common.py:19/81/119/140/144`, `models.py:141-166` |
| **Gateway auth/authz/policy/rate-limit/audit on EVERY request** | org-scope 401/403, per-key allowlist + call cap, per-tool enable/disable, two-tier PII/secret scan with credential hard-block + redaction floor, one audit event per call | `mcp_proxy.py:1562/770/788/418/834/632/690/425` |
| **Broker auth** | fail-closed `X-MCP-Broker-Key` on all `/v1/sandbox/*`; per-org quota | `auth.py:17`, `routes.py:66/149` |
| **SSRF containment** | fail-closed URL guard at registration + oauth discovery; re-guard on derived endpoints; `follow_redirects=False` | `_url_guard.py`, `serializers.py:158`, `mcp_oauth_proxy.py`, `oauth.py` |

### Isolation GAPS (do-not-weaken → strengthen in P7, items #22-25)
Confirmed ABSENT at both broker `_run_kwargs` and image (`broker-sandbox-lifecycle.md`):
`security_opt=no-new-privileges`, `cap_drop=['ALL']`, custom seccomp/AppArmor, `storage_opt`/volume
size cap, `ulimits` (nofile/nproc), run-enforced `user=`, gVisor (only if `MCP_SANDBOX_RUNTIME` set),
egress allowlist, **npm/PyPI package allowlist on the broker path** (the in-process gateway path has
`_PACKAGE_ALLOWLIST`/`_REQUIRE_PINNED_PACKAGES` at `mcp_stdio_adapter.py:362-371`; the broker/agent
path does not), tini/`--init` PID-1, **agent `/rpc` is unauthenticated** (relies solely on per-org net).

---

## 3. Threat model (STRIDE + trust boundaries)

### Trust boundaries (outer → inner)
1. **Browser ↔ Control** — JWT (`AuthContext`); org derived from the authenticated user.
2. **Control ↔ Gateway** — shared internal key + `X-Gateway-*` identity headers (`mcp_proxy._backend_proxy_headers:1672`, `_valid_internal_key:81`).
3. **Gateway ↔ Broker** — `X-MCP-Broker-Key` (fail-closed, `auth.py:17`).
4. **Broker ↔ Sandbox agent** — per-org Docker network; ⚠ agent `/rpc` itself unauthenticated.
5. **Sandbox ↔ untrusted npm/PyPI package** — the innermost, least-trusted boundary; containment = the container + limits.
6. **Org A ↔ Org B** — must be ZERO cross-tenant visibility (the P9 leakage invariant).

### Threats

| # | Threat | Controls present | Residual risk / gap | Where proven/fixed |
|---|---|---|---|---|
| **T1** | **Cross-tenant leakage** (servers/tools/results/creds) | org-scope authz `_validate_org_scope` (401/403, `mcp_proxy.py:1562`); per-org net+vol+container; per-org token namespace (`org|server_url`); per-org scan-version; org-scoped querysets in control | org_slug sanitization divergence (container `-` vs volume `_`, `docker_manager.py:87`) → collision if not normalized; quota TOCTOU/post-restart undercount | **P9 item #30** (canary + egress oracle); harden in #25 |
| **T2** | **Untrusted-package RCE / supply-chain** | container allowlist (npx/node/python…), no-shell exec, per-org sandbox, mem/cpu/pids limits | `node -e`/`python -c` = arbitrary code (inherent — containment is the sandbox); **no package allowlist on broker path**; **no cap_drop/no-new-privileges/seccomp**; no per-child rlimits; unauth agent `/rpc` | **P7 items #22-23** |
| **T3** | **Credential theft** (BYOK / OAuth tokens) | secret env denylist + allowlist passthrough (`mcp_stdio_common.py:19/81`); Fernet at rest; per-org `MCP_REMOTE_CONFIG_DIR`; gateway credential hard-block on args (`mcp_proxy.py:2136`) | egress not restricted (a package could exfiltrate a legitimately-injected token) | **P9 leak tests**; harden egress in #22 |
| **T4** | **SSRF** (registration URL, OAuth metadata endpoints) | fail-closed `is_safe_outbound_url`, re-guard on attacker-controlled derived endpoints, `follow_redirects=False` (`oauth.py`, `mcp_oauth_proxy.py`) | DNS TOCTOU (documented best-effort; fetch-time guard authoritative) | already strong |
| **T5** | **Privilege escalation / container escape** | non-root `USER sandbox`, read-only rootfs, tmpfs noexec on `/tmp` | **no cap_drop, no-new-privileges, seccomp; runc default (shared kernel)**; `/var/npm-cache` tmpfs is exec | **P7 items #22-23** |
| **T6** | **Resource exhaustion / DoS** | per-org rate limits (TPM+RPM), mem/cpu/pids, per-org sandbox quota (50), reaper | reaper has no `try/except` → one Docker error kills reaping permanently (`reaper.py:54`); no disk/storage quota; quota bypass post-restart | **P7 item #24**, **P9 item #29** (503-storm/reuse) |
| **T7** | **Provisioning race** (B3) | client 503-retry + broker `_post_agent_rpc` 8-retry | readiness = container `running` not agent-bound; `ensure` ignores `warm`; no eager provision | **B3 items #19-21** |
| **T8** | **Audit gaps / spoofed events** | one audit event per call (`_record_gateway_event:425`); internal-only record-event view; decision validated | audit is best-effort (never blocks hot path) — acceptable | present |

---

## 4. The four confirmed bugs — root cause / current state / remaining

> ⚠️ Per `frontend-panel-flow.md`: B1/B2/B4 carry substantial prior fixes (prior commit `60f28c0c`
> "Enhance MCP OAuth handling and fix transport validation"). Fix items are **verification-first**.

### B1 — oauth+stdio selectable / two Authorize buttons / "Server has no URL"
- **Root cause:** OAuth 2.1 auth-code is HTTP-only (needs an HTTP MCP URL for RFC 9728/8414 discovery).
  A stdio row has no top-level URL (its URL, for mcp-remote, lives in args), so persisting
  `auth_type="oauth"` on a stdio/websocket row produced a URL-less "oauth" server → a broken second
  Authorize button that 400s "Server has no URL".
- **Current state:** form filters the `oauth` option to `streamable-http`/`sse` only
  (`MCPConnectorPanel.jsx:1483-1490`) + drops oauth on transport switch (`:1385`); backend registration
  guard rejects oauth+non-HTTP (`serializers.py:177`, test `test_oauth_transport_guard.py`); cards
  render exactly one mutually-exclusive Authorize button (`:1166` gateway / `:1180` control), and the
  control button requires `!!srv.url` so "Server has no URL" is unreachable from it.
- **Remaining (item #13, verify+finish):** the **duplicate/broken control authorize path still exists
  end-to-end**: frontend `startControlOAuth:845` → control `MCPServerOAuthStartView` (`views.py:2513`)
  which emits `400 "Server has no URL"` (`:2526`) and **sets `auth_type="oauth"` bypassing the
  registration transport guard** (`:2582`). B1 spec = delete/neutralize that path so `:2526` is
  structurally unreachable (route http-oauth through the gateway path, or add the transport guard to
  the view). Then Playwright-verify: stdio server → no OAuth button; HTTP oauth → exactly one Authorize + popup.

### B2 — fresh HTTP-oauth server shows a normal 0-tools card
- **Root cause:** register never syncs (`views.py:764`) and the OAuth callback flips `oauth_authorized`
  True but does not auto-sync (`:2658`); a fresh oauth server therefore has 0 tools. If the UI renders a
  generic "0 tools / Unknown" card it misleads the operator into a pre-auth sync that fails.
- **Current state:** `serverAwaitingAuth:741` renders an `"authorization required"` badge (`:1095-1097`);
  `syncBlockedForAuth:757` disables sync until authorized; backend exposes `oauth_authorized`,
  `needs_reauth`, `connection_status`; a pre-auth discover returns `([], "needs re-authentication")`
  (`views.py:448/452`) → `connection_status="failed"`.
- **Remaining (item #15, verify):** in-browser confirm a freshly-registered HTTP oauth server renders a
  *distinct* pending state (not a plain 0-tools card) and that tools populate after authorize + sync
  (`startControlOAuth` auto-syncs on success, `:883`).

### B3 — "MCP sandbox is temporarily unavailable" on first tool call
- **Root cause:** the exact string originates at `mcp_sandbox_client.py:58` (broker 502) and `:100`
  (broker unreachable after `_RETRY_MAX=5`). (a) `broker_send_jsonrpc:136` never calls `ensure_sandbox`
  first (**no eager provisioning**); (b) `_request_with_503_retry:104` retries **only 503** — a cold-start
  502 is not retried; (c) broker readiness = container `running` (`docker_manager.py:132`), NOT
  agent-socket-bound, and `ensure` ignores `warm` (`routes.py:153`); (d) broker over-broadens every agent
  HTTP ≥400/≥500 to 502 (`routes.py:189-198`). Surfaced via `_adapter_forward` broad except
  (`mcp_proxy.py:1832`).
- **Current state:** partial mitigation only — client 503-retry/backoff + broker `_post_agent_rpc`
  8-retry (`routes.py:106`). No eager provision, no readiness poll, no register/authorize→provision hook.
- **Remaining (items #19-21 — the real backend work):** eagerly provision the per-org sandbox on
  register/authorize/first-sync (control→gateway `ensure`); add a readiness poll + bounded backoff in
  `mcp_sandbox_client` treating provisioning distinctly; broker returns a clear "provisioning" 503 (not a
  collapsed 502) during cold start, and stops conflating real agent tool errors with unavailability.

### B4 — Add Server modal loses input focus per keystroke
- **Root cause (classic React):** either a field component defined *inside* render (new identity each
  render → remount → focus loss), or a modal effect that tears down/re-runs per keystroke and
  re-autofocuses the first element, or portal children recreated with unstable keys.
- **Current state (root causes addressed at code level):** **no component defined inside render** — the
  render helpers are function *calls* (`{renderServers()}`/`{renderTools()}` `:2203-2208`), not
  `<Component/>`; `ui/Dialog.jsx` keeps `onClose` in a ref (`:20-24`) so `handleKey` is stable
  (`useCallback([])`) and the focus-trap/mount effect deps are `[open, handleKey]` (`:69`) → it runs only
  when `open` toggles, not per keystroke. Fields are plain controlled inputs at stable positions.
- **Remaining (items #17-18, verify):** Playwright confirmation that typing a long string into each modal
  field keeps focus per keystroke and presets prefill correctly. Minor: auth-header rows use index keys
  (`:1534`) — fine for append/edit.

---

## 5. Scale/leakage proof plan (P8/P9 — items #26-31)
3 orgs × 5 MCP servers = 15 parallel MCPs. Deterministic checks via Everything.echo/add.
- **Concurrency (#28):** parallel tool calls across all 15 → correct isolation, no mixed/dropped responses.
- **Load (#29):** sustained calls → sandbox reuse holds, no exhaustion, no 503 storms, limits respected;
  watch the reaper-resilience gap (T6) and quota TOCTOU.
- **Leakage (#30):** Org A can never list/call Org B's servers/tools/results/creds; plant a canary in one
  org and prove no other org observes it on any channel; **captured egress bytes are the truth**, cross-checked
  with `aidefence_scan`/`aidefence_has_pii` as an independent oracle (T1/T3).
- **OAuth/transport correctness under load (#31):** no stdio server ever attempts OAuth; HTTP oauth
  servers authorize cleanly.
