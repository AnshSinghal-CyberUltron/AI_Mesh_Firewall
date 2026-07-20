# `mcp-remote` — stdio↔remote-HTTP bridge + OAuth (P1 item #8)

> Deliverable for `scripts/ralph/mcp_progress.md` **P1 item #8**. Source: `geelen/mcp-remote` `main`
> (README + `src/proxy.ts`, fetched via GitHub MCP). Clarifies the Linear stdio+remote case and
> validates the gateway's `--header` injection + per-org `MCP_REMOTE_CONFIG_DIR` design.

## What it is
`mcp-remote` connects an MCP client that only speaks **local stdio** to a **remote HTTP/SSE MCP
server, with OAuth**. It is a bidirectional proxy: a local `StdioServerTransport` (talks JSON-RPC to
whoever spawned it) ↔ a remote SSE/HTTP transport (`connectToRemoteServer`), wired by
`mcpProxy({transportToClient, transportToServer})` (`proxy.ts`). Launch:
`npx [-y] mcp-remote https://remote.mcp.server/sse [callback-port] [flags]`.

## How its OAuth works (and how the gateway bypasses it)
- **Lazy auth**: `createLazyAuthCoordinator` — OAuth only initializes when a request returns 401.
  Discovery is RFC 9728 PRM (`discoverOAuthServerInfo` probes `WWW-Authenticate` → PRM →
  authorization server), then `NodeOAuthClientProvider` runs the OAuth 2.1 + PKCE flow using a **local
  callback HTTP server** (default port **3334**) and **opens a browser**.
- **`--header "Authorization: Bearer <token>"` bypasses OAuth entirely** (README: *"To bypass
  authentication, or to emit custom headers on all requests to your remote server, pass `--header`"*).
  In `proxy.ts` the parsed `headers` are forwarded to `connectToRemoteServer` + `discoverOAuthServerInfo`
  on **every** request. If the injected Bearer satisfies the remote server, no 401 fires, so the lazy
  auth coordinator (browser flow) never runs.
- **Token storage**: `~/.mcp-auth` by default, or **`MCP_REMOTE_CONFIG_DIR`** (README troubleshooting +
  `NodeOAuthClientProvider`). Sessions are isolated per `(serverUrlHash, resource, headers)`; the
  `--resource` flag (RFC 8707 resource indicator) separates OAuth sessions for multi-tenant use.
- Other flags: `--transport http-first|sse-first|http-only|sse-only`, `--allow-http` (default HTTPS
  only), `--static-oauth-client-info`/`--static-oauth-client-metadata` (for servers without DCR),
  `--ignore-tool <glob>` (filters tools), `--host`, `--auth-timeout`, `--debug`.

## How THIS repo integrates it (validated against the code maps)
1. A remote HTTP MCP server (e.g. **Linear**, `https://mcp.linear.app/sse`) is registered as
   **transport=`stdio`**, `command=npx`, `args=[mcp-remote, https://mcp.linear.app/sse]`. Its control
   `auth_type` stays `"none"` — the gateway, not the control row, owns the OAuth.
2. The gateway does the OAuth 2.1 dance itself (`mcp_oauth_proxy.py`, per-org, PKCE, RFC 9728/8414/8707)
   and stores the token per-org (`_token_save` keyed `org|server_url`).
3. On each stdio tool call, `_maybe_inject_oauth_header` (`mcp_proxy.py:1732`) scans args for
   `mcp-remote`, extracts the wrapped URL, fetches the stored token, and appends
   `--header Authorization: Bearer <token>` (`:1759`). → mcp-remote runs **headless** (no browser),
   forwarding that Bearer to the remote server.
4. The broker sets **`MCP_REMOTE_CONFIG_DIR=/data/mcp-auth`** per-org (`docker_manager.py:262`) and
   `_write_mcp_remote_tokens` writes token files into the per-org dir — so even if mcp-remote reads
   from disk, tokens are **per-org isolated** (matches mcp-remote's `MCP_REMOTE_CONFIG_DIR` override).
5. If no/expired token → the remote 401s → mcp-remote tries its **browser** OAuth (impossible in the
   headless sandbox) → the repo's `_looks_like_oauth_prompt` + `_flag_needs_reauth`
   (`mcp_stdio_adapter.py` / `stdio_manager.py`) detect the prompt and surface **`needs_reauth`**
   instead of hanging (the gateway `_notify_control_needs_reauth:1698` backprops it).

## The Linear stdio+remote case — B1 nuance, definitively
Linear is a **remote HTTP MCP server** that requires OAuth, accessed **through a stdio wrapper**
(`mcp-remote`). So a `stdio` row **legitimately carries an HTTP URL in its args** and **does OAuth** —
but the OAuth is against the *wrapped HTTP URL*, handled gateway-side, with `auth_type="none"` on the
row. This is why:
- B1's rule "block oauth+stdio" means block **`auth_type="oauth"` on a pure-local-stdio (URL-less)
  row**, NOT block stdio-via-mcp-remote. The frontend correctly routes stdio-mcp-remote to the
  **gateway** OAuth path (`serverNeedsOAuth:711` → `startOAuth:773`), distinct from HTTP-oauth's
  control path.
- The MCP auth spec (item #7) agrees: STDIO transports "SHOULD NOT follow" the OAuth spec directly and
  should use env creds — mcp-remote is the bridge that turns the stdio case into an HTTP OAuth case.

## Implications / notes for later items
- **Headless-OAuth is solved by pre-injection**: the gateway must always inject the `--header` before
  spawning mcp-remote so the browser flow never triggers in the sandbox. Verify under load (#31) that
  a stdio-mcp-remote server with a valid stored token calls tools without ever hitting the OAuth prompt.
- **Supply-chain / egress**: `mcp-remote` is itself an npm package run inside the sandbox and it makes
  outbound HTTPS to the remote MCP server — reinforces the P7 gaps "no egress allowlist" and "no
  package allowlist on the broker path" (items #22-23). Keep `--allow-http` OFF (HTTPS-only default).
- **`--ignore-tool`** could complement the gateway's per-tool enable/disable, but the repo enforces tool
  policy at the gateway (`_is_tool_disabled`) for ALL transports — do not rely on mcp-remote's filter
  (it runs inside the untrusted sandbox and is bypassable).
- Cold-start (B3): first call pays `npx` fetch of `mcp-remote` AND its connection/discovery to the
  remote server — a longer readiness window than a pure-local stdio server; the B3 readiness probe must
  allow for it (init timeout already 120s).

Sources: [geelen/mcp-remote README](https://github.com/geelen/mcp-remote/blob/main/README.md),
[proxy.ts](https://github.com/geelen/mcp-remote/blob/main/src/proxy.ts).
