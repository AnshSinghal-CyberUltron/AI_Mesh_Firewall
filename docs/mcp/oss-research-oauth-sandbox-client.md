# MCP OAuth in the sandboxed agent — control vs data plane (P2.7)

> Deliverable for Ralph scratchpad **P2 item #7** and `scripts/ralph/mcp_progress.md` P2 web research.
> Complements `oss-research-oauth-spec.md` (compliance matrix), `oss-research-oauth-transport-ux.md`
> (B1/B2 UX table), and `oss-research-mcp-remote.md` (Linear bridge). Feeds **P3 #8**
> (`SANDBOX_TRANSPORT_CONTRACT`) and **P4 #9–11** (in-sandbox HTTP/WS proxy).

## 1. PKCE + RFC compliance matrix (repo, 2026-07-02)

| Requirement | RFC / spec | Repo role | Implementation | Status |
|-------------|-----------|-----------|----------------|--------|
| PKCE S256 mandatory | OAuth 2.1 §7.5.2, RFC 7636 | OAuth **client** (gateway/control) | `generate_pkce` → `code_challenge_method=S256` on authorize | ✅ |
| Refuse if AS lacks PKCE | MCP 2025-11-25 | OAuth client | Verify `code_challenge_methods_supported` includes S256 before authorize | ⚠️ verify upstream probe |
| PRM discovery | RFC 9728 | OAuth client | 401 `WWW-Authenticate` → `resource_metadata` URL → GET PRM; fallback `/.well-known/oauth-protected-resource` (SEP-985) | ✅ gateway + control |
| AS metadata | RFC 8414 | OAuth client | `_fetch_as_metadata` / gateway `/.well-known/oauth-authorization-server` | ✅ |
| DCR | RFC 7591 | OAuth client | `register_client` control; gateway proxy DCR | ✅ |
| `resource` param (authorize + token + refresh) | RFC 8707 | OAuth client | `canonical_resource` on all three requests | ✅ |
| HTTPS endpoints + redirect URI rules | MCP auth spec | OAuth client | localhost/HTTPS redirect validation; SSRF guards on derived URLs | ✅ |
| `state` CSRF | OAuth 2.1 | OAuth client | single-use state on callback | ✅ |
| Token passthrough forbidden | MCP auth spec | Gateway proxy | Separate per-org upstream token; never forward inbound gateway token | ✅ |
| STDIO SHOULD NOT use OAuth spec | MCP auth spec | Registration | `serializers.py` blocks `auth_type=oauth` unless HTTP transport | ✅ B1 |
| RS audience validation | MCP auth spec | Upstream RS | Delegated to upstream MCP server | N/A (client only) |
| OAuth in sandbox subprocess | — | **Must NOT** | Sandbox runs data-plane only (see §4) | ✅ by design (mcp-remote bypass) |

**SEP-985 (Final):** clients MUST parse `WWW-Authenticate` on 401; if `resource_metadata` absent, fallback
probe `/.well-known/oauth-protected-resource`. Repo gateway path already probes; ensure control path matches.

## 2. Why OAuth requires an HTTP transport URL

OAuth for MCP is a **remote-server** protocol. Every step assumes an HTTP(S) anchor:

| Step | Why HTTP URL is required |
|------|--------------------------|
| PRM discovery (RFC 9728) | Metadata document is at `{server-url}/.well-known/oauth-protected-resource` or path-relative variant |
| 401 challenge | `WWW-Authenticate: Bearer resource_metadata="https://…"` points to PRM |
| RFC 8707 `resource` | MUST be canonical HTTP(S) URI of the MCP resource server |
| AS metadata (RFC 8414) | Fetched over HTTPS from authorization server issuer |
| Browser authorize | User redirected to HTTPS authorize endpoint; callback to localhost/HTTPS redirect URI |
| Token endpoint | HTTPS POST with `code` + `code_verifier` + `resource` |

A **pure stdio subprocess** has no HTTP listener, no canonical `resource` URI, and no place to host PRM.
The spec therefore states: *"Implementations using an STDIO transport SHOULD NOT follow this
specification, and instead retrieve credentials from the environment."*

**Implication for ZeroShield:** `auth_type=oauth` is valid only when `transport ∈ {streamable-http, sse}`
AND `url` is set. Blocking oauth+stdio at registration is **spec-mandated**, not a product quirk.

## 3. stdio + mcp-remote vs `auth_type=oauth`

These are **different registration and OAuth paths**:

| Aspect | stdio + mcp-remote (Linear) | `auth_type=oauth` (HTTP row) |
|--------|------------------------------|------------------------------|
| Transport | `stdio` | `streamable-http` or `sse` |
| URL field | empty (URL in `args`) | required `url` column |
| `auth_type` | **`none`** | **`oauth`** |
| OAuth client | **Gateway** (`mcp_oauth_proxy`) | Gateway or control (unify → gateway) |
| Authorize UI | `serverNeedsOAuth` → gateway `oauth/start` | HTTP-oauth → single Authorize |
| Token injection | `--header Bearer` into mcp-remote args before sandbox spawn | Bearer on upstream HTTP calls (gateway today; sandbox agent P4) |
| Browser OAuth in sandbox | **No** — gateway pre-injects token; mcp-remote lazy OAuth disabled | **No** — gateway/control owns browser flow |
| Spec basis | stdio env-creds pattern + HTTP bridge (mcp-remote) | Full MCP OAuth spec for HTTP transport |

**B1 rule:** block `auth_type=oauth` on URL-less stdio — **not** block stdio+mcp-remote gateway authorize.

**UX (R3 vs R5):** both show pending state + one Authorize button, but mutually exclusive conditions
(see `oss-research-oauth-transport-ux.md` §4).

## 4. In-sandbox OAuth client — what to do and what NOT to do

### Principle: OAuth control plane stays outside the sandbox

```
┌─────────────────────────────────────────────────────────────────┐
│ Control plane (gateway + control)                                │
│  • PRM / AS discovery (RFC 9728, 8414)                          │
│  • DCR (RFC 7591)                                                │
│  • PKCE authorize + browser redirect + callback (localhost)      │
│  • Token exchange + refresh (RFC 8707 resource param)            │
│  • Encrypted token storage per org|server_url                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ pre-issued Bearer / session config
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│ Per-org gVisor sandbox (data plane only)                         │
│  stdio: spawn with injected env/args (no OAuth client)           │
│  HTTP/SSE: httpx client + Authorization: Bearer <token>        │
│  WS: websockets client + Authorization header at connect         │
│  Egress: allowlisted upstream host only (P2.6 H13)               │
└─────────────────────────────────────────────────────────────────┘
```

### Why NOT run OAuth inside the sandbox

| Risk | Detail |
|------|--------|
| Browser callback | mcp-remote opens browser + localhost:3334 callback — impossible/unsafe headless in tenant sandbox |
| Client credentials | DCR `client_secret` / PKCE verifier in attacker-controlled process memory |
| Token exfil | OAuth tokens in sandbox env readable by malicious npm package |
| Redirect URI | Sandbox cannot host stable redirect URI for multi-tenant gateway |
| Spec alignment | MCP positions stdio as env-creds; HTTP OAuth is for remote servers accessed over network |

### Current pattern (stdio + mcp-remote) — acceptable bridge

Gateway completes OAuth → stores token → injects `--header Authorization: Bearer` → broker spawns
mcp-remote in sandbox. mcp-remote never runs lazy browser OAuth if Bearer is valid.

### Target pattern (P4 native HTTP/WS in sandbox)

1. Gateway/control completes OAuth (unchanged).
2. Broker RPC to sandbox agent includes **ephemeral upstream config**:
   - `upstream_url`, `transport`, `authorization: Bearer <token>` (or reference to per-org volume path).
3. Sandbox **httpx** / **websockets** client attaches Bearer on every upstream request.
4. Agent **never** implements PRM, DCR, PKCE, or browser redirect.
5. On 401 from upstream → return `needs_reauth` to gateway (same as `_looks_like_oauth_prompt` today).

### Contract fields for P3 #8 (`SANDBOX_TRANSPORT_CONTRACT`)

```json
{
  "transport": "streamable-http | sse | websocket",
  "upstream_url": "https://mcp.example.com/mcp",
  "auth": {
    "type": "bearer_injected",
    "header": "Authorization",
    "value_ref": "volume:/data/mcp-auth/{server_slug}/token"
  },
  "oauth_client_role": "forbidden_in_sandbox"
}
```

OAuth refresh remains gateway/control responsibility; sandbox returns `needs_reauth` on upstream 401.

## 5. HTTP vs WebSocket OAuth in sandbox (data plane)

| Transport | Upstream auth | Sandbox client | OAuth client in sandbox? |
|-----------|---------------|----------------|--------------------------|
| streamable-http | Bearer on POST /mcp | `httpx.AsyncClient` + default headers | **No** |
| sse (legacy) | Bearer on GET /sse + POST messages | `httpx` + SSE stream | **No** |
| websocket | Bearer in `Authorization` header at upgrade | `websockets.connect(extra_headers=…)` | **No** |
| stdio + mcp-remote | Bearer via `--header` arg | subprocess spawn (current) | **No** (mcp-remote OAuth bypassed) |

**Token refresh:** gateway background refresh before expiry; on failure set `needs_reauth` on control row.
Sandbox does not hold refresh tokens unless stored in per-org volume **read-only to agent** — prefer
gateway injects short-lived access token per RPC to minimize exposure.

## 6. Recommendations for P3–P4

1. **Ratify in contract:** `oauth_client_role: external_only` — sandbox agent MUST NOT implement OAuth client.
2. **Unify authorize paths** (B1 #13): HTTP-oauth and mcp-remote both use gateway `oauth/start`.
3. **P4 HTTP proxy:** pass Bearer from gateway in RPC envelope; agent uses httpx only.
4. **P4 WS proxy:** pass Bearer at connection setup; no OAuth handshake in sandbox.
5. **Deprecate mcp-remote for HTTP rows** once native in-sandbox httpx path is verified (P2.5).
6. **#31 fleet test:** assert zero PRM/DCR/PKCE traffic originates from sandbox egress (only upstream MCP + registry).

## Sources

- [MCP Authorization 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- [MCP Authorization 2025-11-25](https://mcp.mintlify.app/specification/2025-11-25/basic/authorization) — PKCE refusal, resource param
- [SEP-985 PRM fallback](https://modelcontextprotocol.org/seps/985-align-oauth-20-protected-resource-metadata-with-rf)
- [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728), [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414), [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html)
- Internal: `oss-research-oauth-spec.md`, `oss-research-oauth-transport-ux.md`, `oss-research-mcp-remote.md`, `oss-research-remote-transport-proxies.md`
