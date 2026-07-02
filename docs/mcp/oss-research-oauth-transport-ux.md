# OAuth-for-MCP transport rules + the B1/B2 UX decision table (P2 item #10)

> Deliverable for `scripts/ralph/mcp_progress.md` **P2 item #10**: *why OAuth needs an HTTP URL* and
> *the correct UX for the stdio-wrapped-remote case*. This is the **product spec** the B1/B2 fixes
> (items #13–16) implement and verify against. It synthesizes the spec/OSS research (items #7 `oss-
> research-oauth-spec.md`, #8 `oss-research-mcp-remote.md`) and the current-code map (item #4
> `frontend-panel-flow.md`) into ONE decision table, validated against how real MCP clients (VS Code,
> Claude Desktop/Code) present OAuth. Sources fetched via WebFetch/WebSearch.

## 1. Transport taxonomy (MCP spec, definitive)
MCP defines exactly **two standard transports** (`modelcontextprotocol.io/docs/concepts/transports`):

| Transport | Shape | Has a URL? | OAuth applies? | Credentials |
|---|---|---|---|---|
| **stdio** | client launches server as a **subprocess**, JSON-RPC over stdin/stdout | **No** | **No** — spec: STDIO *"SHOULD NOT follow this specification, and instead retrieve credentials from the environment"* | env vars (BYOK) |
| **Streamable HTTP** | server is an independent process at a **single HTTP endpoint** (`https://…/mcp`, POST+GET, optional SSE) | **Yes** | **Yes** — OAuth 2.1 discovery (RFC 9728 PRM + 8414 AS metadata) is fetched over HTTP from that URL | OAuth 2.1 / bearer / BYOK header |
| **HTTP+SSE** (`sse`) | **deprecated** (protocol 2024-11-05), replaced by Streamable HTTP; still supported for back-compat | **Yes** | **Yes** (it is an HTTP transport with a URL) | same as Streamable HTTP |

→ The repo's transport enum `stdio / streamable-http / sse` maps 1:1. **OAuth is structurally an
HTTP-transport-only concept**: the RFC 8707 `resource` param MUST be the canonical HTTP(S) URI of the
server, and PRM/AS-metadata discovery is HTTP. A pure-local stdio subprocess has no URL to discover,
protect, or name as a `resource` → `auth_type="oauth"` on a stdio row is **spec-invalid** (item #7 §3).

## 2. The stdio-wrapped-remote exception (mcp-remote) — the B1 nuance
`mcp-remote` is a **stdio subprocess that wraps a remote HTTPS MCP URL** and does the HTTP OAuth dance
against *that wrapped URL* (item #8). So a `stdio` row **legitimately carries an HTTPS URL in its args**
(`args=[mcp-remote, https://mcp.linear.app/sse]`) and **does OAuth** — but:
- the control row keeps `auth_type="none"` (the **gateway**, not the control OAuth column, owns the token);
- OAuth is initiated via the **gateway** path (`serverNeedsOAuth:711 → startOAuth:773`), NOT the control
  http-oauth path;
- the gateway pre-injects `--header Authorization: Bearer <token>` so mcp-remote runs headless.

**Therefore "block oauth+stdio" (B1) means: block `auth_type="oauth"` on a *URL-less local stdio* row —
NOT block stdio-via-mcp-remote.** These are two different things and the UX must keep them distinct.

## 3. Industry UX norms (validate the target shape)
How VS Code / Claude Desktop/Code present remote-server OAuth (WebSearch, incl. anthropics/claude-code
issues #42359, #55021):
- A per-server **status indicator**: `Needs Auth` before authorization (≡ the repo's *"authorization
  required"* badge, B2 pending state).
- **Exactly one** `Authenticate` button per server; clicking it **opens the browser** OAuth page, then the
  UI shows a transitional **"Completing authentication in browser…"** state.
- Tools/capabilities are usable **only after** auth completes; an unauthorized OAuth server is never shown
  as a ready "0 tools" card.
- Known real bug to guard against in B1 verification (issue #42359): the Authenticate button *not opening
  the browser* — so B1's Playwright check (#14) must assert the popup actually opens.

The repo's intended shape (badge + single Authorize button + popup + poll→auto-sync) matches these norms.

## 4. THE DECISION TABLE — (transport × auth_type) → form + controls  ⟵ implement/verify against this
`T` = transport, `A` = auth_type. "BYOK" = token/basic/header/query_param.

| # | T | A | Form: is A=oauth selectable? | Authorize control | OAuth path | Card state after register | Tools sync |
|---|---|---|---|---|---|---|---|
| R1 | stdio (no mcp-remote) | none / BYOK | **NO** (filtered out) | **none** | — | normal card | on spawn+`initialize` |
| R2 | stdio (no mcp-remote) | oauth | **BLOCKED** — invalid; form must not allow, serializer must reject | **none** | — | (unreachable) | — |
| R3 | **stdio + mcp-remote** (args wrap https URL) | none (row) | N/A (row stays none) | **exactly one — GATEWAY** (`serverNeedsOAuth`→`startOAuth`) | gateway `POST /gateway/{org}/mcp/{slug}/oauth/start` | **pending** ("authorization required") until gateway token | after gateway token + sync |
| R4 | streamable-http / sse | none / BYOK | (oauth not chosen) | **none** | — | normal card | on connect+`initialize` |
| R5 | **streamable-http / sse** | **oauth** | **YES** (only here) | **exactly one — HTTP-oauth** | unified authorize (see §5) | **pending** ("authorization required"); `syncBlockedForAuth` gates sync | **only after** `oauth_authorized` + sync |
| R6 | websocket / other | oauth | **NO** (form allows oauth only for streamable-http/sse) | none | — | normal card | — |

Invariants the table encodes (B1+B2):
- **oauth is selectable ONLY for `streamable-http`/`sse`** (R5). Blocked for stdio (R2) and ws/other (R6).
- **At most ONE Authorize button per card**, because the gateway condition (R3, requires stdio+mcp-remote)
  and the http-oauth condition (R5, requires HTTP transport + url) are **mutually exclusive** (item #4 §Authorize).
- **A fresh OAuth server (R3/R5) shows a distinct *pending* state, never a ready 0-tools card** (B2);
  tools appear only after `oauth_authorized` + sync.
- **"Server has no URL" must be structurally unreachable** — no code path may attempt HTTP OAuth on a
  row without an HTTP url (see §5).

## 5. B1 fix direction (what #13 must decide/do) — from the current-code map
Current code (item #4) already has the form guard (`:1483-1490`), the transport→auth reset (`:1385-1392`),
and mutually-exclusive buttons. **The remaining B1 defect is a duplicate/broken *control* authorize path
that is still reachable end-to-end**: `startControlOAuth (:845) → control MCPServerOAuthStartView
(views.py:2513)` which can emit `400 "Server has no URL"` (`:2526`) and set `auth_type="oauth"` bypassing
the registration guard (`:2582`). B1 spec = **make that path structurally unreachable**:
- **Recommended (unify):** route *all* HTTP-oauth authorize through **one** path. Given the gateway already
  owns per-org token storage, PKCE, RFC 9728/8414/8707 (item #7) and the mcp-remote case, unifying HTTP-oauth
  onto the gateway `oauth/start` path (like R3) collapses R3+R5 to a single authorize code path → deletes the
  "no URL" branch entirely and removes the guard-bypass. (Alternative: keep control path but hard-gate it on
  `url && transport∈(http,sse)` server-side AND remove its `auth_type="oauth"` side-effect so it cannot
  bypass `serializers.py:177`.) #13 picks one; either way the "no URL" 400 must become unreachable.
- Whatever path remains, the button that renders it must be the *single* Authorize (R5), and stdio rows
  (R2) must never render or reach it.

## 6. Verification hooks for #14/#16/#31
- #14 (B1): register Linear-stdio(mcp-remote) → **no** http-oauth button, **no** "no URL" error, gateway
  Authorize present + popup opens; register a streamable-http oauth server → **exactly one** Authorize,
  popup opens (guard against issue #42359's silent no-op).
- #16 (B2): register streamable-http oauth → **pending** badge (not 0-tools card) → authorize → poll
  `oauth_authorized` → tools populate on auto-sync.
- #31 (fleet under load): assert **no stdio server ever attempts the OAuth spec flow** (env/mcp-remote
  only); HTTP-oauth servers complete discovery→(DCR)→PKCE→token with the `resource` param and authorize
  cleanly; expired refresh surfaces `needs_reauth`.

## Sources
- [MCP Transports (concepts)](https://modelcontextprotocol.io/docs/concepts/transports) — two standard transports (stdio, Streamable HTTP); HTTP+SSE deprecated; stdio = subprocess/no-URL.
- [MCP Authorization 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) — STDIO SHOULD NOT follow the OAuth spec; HTTP transports SHOULD; `resource` = canonical HTTP URI (see item #7 doc).
- VS Code / Claude MCP OAuth UX: [Claude Code MCP OAuth guide](https://claudelab.net/en/articles/claude-code/claude-code-mcp-oauth-authentication-guide), [MCP auth in Claude Code](https://www.truefoundry.com/blog/mcp-authentication-in-claude-code), [issue #42359 (Authenticate button no-op)](https://github.com/anthropics/claude-code/issues/42359), [issue #55021 (Needs Auth status)](https://github.com/anthropics/claude-code/issues/55021).
- Companion repo docs: `oss-research-oauth-spec.md` (#7), `oss-research-mcp-remote.md` (#8), `frontend-panel-flow.md` (#4).
