# MCP Authorization spec + OAuth 2.1 — requirements & repo compliance (P2 item #7)

> Deliverable for `scripts/ralph/mcp_progress.md` **P2 item #7**. Source: MCP Authorization spec
> **2025-06-18** (fetched via WebFetch) + RFC 9728/8414/7591/8707/OAuth 2.1-draft-13. Validates the
> gateway + control OAuth implementation against the spec and confirms the B1 invariant.

## 1. The spec flow (client's job — which is what THIS repo implements)
1. MCP request without token → server `401` + `WWW-Authenticate` with `resource_metadata` URL.
2. Client GETs `/.well-known/oauth-protected-resource` (RFC 9728 PRM) → `authorization_servers[]`.
3. Client GETs AS `/.well-known/oauth-authorization-server` (RFC 8414 metadata).
4. (opt) DCR `POST /register` (RFC 7591) → client_id.
5. PKCE authorize (S256) **+ `resource` param** → user consents → code callback.
6. Token request **+ code_verifier + `resource` param** → access token (+ refresh).
7. MCP requests carry `Authorization: Bearer <token>`; server validates audience.

## 2. Spec MUST/SHOULD → repo compliance matrix

| Spec requirement (2025-06-18) | Repo implementation | Status |
|---|---|---|
| Auth is **OPTIONAL**; **STDIO SHOULD NOT follow this spec** — "retrieve credentials from the environment"; HTTP-based transports SHOULD conform | control blocks `auth_type=="oauth"` unless transport∈(streamable-http,sse) (`serializers.py:177`); stdio uses BYOK env or mcp-remote (HTTP-wrapper) | ✅ **B1 invariant is spec-mandated** |
| MCP server (RS) MUST implement RFC 9728 PRM + `WWW-Authenticate` on 401; **client MUST use PRM** for AS discovery | client discovers via PRM: control `oauth.discover:169` (401 `WWW-Authenticate` → `_probe_resource_metadata_url:114` → PRM); gateway `mcp_oauth_proxy._discover_oauth_metadata:283`. Repo's own gateway self-AS also serves PRM (`mcp_oauth.py:99`) | ✅ (client) |
| Client MUST use RFC 8414 AS metadata | control `_fetch_as_metadata:149`; gateway serves `/.well-known/oauth-authorization-server` (`mcp_oauth.py:140`) | ✅ |
| DCR (RFC 7591) SHOULD be supported | control `register_client:237`; gateway proxy DCR (`mcp_oauth_proxy.py:472`); gateway self-AS `oauth_register` (`mcp_oauth.py:167`) | ✅ |
| Client MUST implement **PKCE (S256)** | control `generate_pkce:48` + `code_challenge_method=S256` on authorize (`oauth.py:284`); gateway self-AS verifies PKCE (`mcp_oauth.py:527`) | ✅ |
| **`resource` param (RFC 8707) MUST be in BOTH authorize AND token requests**, canonical URI, sent regardless of AS support | control includes `resource` on authorize (`oauth.py:286`), exchange (`:337`), refresh (`:355`); `canonical_resource:67` builds the RFC 8707 URI | ✅ **verified all 3** |
| All AS endpoints MUST be HTTPS; redirect URIs MUST be localhost/HTTPS | gateway `_validate_redirect_uri:80` (localhost/HTTPS/vscode|cursor); SSRF guard + `follow_redirects=False` on every derived endpoint (`oauth.py:309`, `mcp_oauth_proxy.py`) | ✅ (+ SSRF hardening beyond spec) |
| Client SHOULD use + verify `state` (CSRF) | control `generate_state:60`, single-use match on callback (`views.py:2672`); gateway proxy signed flow state | ✅ |
| Redirect URIs MUST be pre-registered + exact-match | gateway self-AS validates against registered value | ✅ |
| **Token passthrough FORBIDDEN**: MCP server MUST NOT forward the client's token to upstream; upstream token is separate | repo obtains a **separate per-org upstream token** (`_token_save` keyed `org|server_url`), stores encrypted, injects THAT as bearer (`_maybe_inject_oauth_header:1759`) — never forwards the inbound gateway token | ✅ avoids confused-deputy |
| RS MUST validate token audience (issued for it) | gateway self-AS returns the **gateway API key** as the `access_token` (`mcp_oauth.py:542`), validated by `AuthMiddleware` — audience-bound to the gateway by construction (not a JWT-aud check) | ⚠️ non-standard but sound (see notes) |
| Public clients: refresh tokens MUST rotate | upstream AS controls rotation; repo persists whatever the provider returns (`_token_save`/`refresh_access_token:342`) | ⚠️ delegated to upstream AS |

## 3. Why OAuth requires an HTTP URL (definitive B1 basis)
The spec: *"Implementations using an STDIO transport **SHOULD NOT** follow this specification, and
instead retrieve credentials from the environment."* OAuth discovery is inherently HTTP — RFC 9728 PRM
and RFC 8414 AS metadata are fetched over HTTP(S) from the **resource server URL**, and the RFC 8707
`resource` param **MUST** be the canonical HTTP(S) URI of the MCP server. A pure-local stdio process
has no such URL, so `auth_type="oauth"` on a stdio row is spec-invalid → the repo's registration guard
(`serializers.py:177`) and the frontend form filter (`MCPConnectorPanel.jsx:1483-1490`) are correct.
The exception is **mcp-remote**: a stdio process that *wraps a remote HTTP MCP URL* and performs the
HTTP OAuth dance against that URL (studied in item #6/#8) — which is why a stdio row can legitimately
carry an HTTP URL in its args (the B1 nuance), handled by `_maybe_inject_oauth_header`.

## 4. Notes / verification points for later items
- The gateway plays **two OAuth roles**: (a) a self-**AS** for MCP clients like VS Code (`mcp_oauth.py`)
  where the issued `access_token` IS the gateway API key — pragmatic, audience-bound by construction,
  validated by `AuthMiddleware`; and (b) an OAuth **client** to upstream MCP servers (`mcp_oauth_proxy.py`
  + control `oauth.py`). It is never a spec resource-server doing JWT-audience validation — acceptable
  for a gateway/proxy, but note it if a future item adds JWT-bearer upstreams.
- **Token-passthrough compliance is a strength to preserve** (T2/confused-deputy in the threat model):
  never forward the inbound gateway token to an upstream; always use the separate per-org stored token.
- Under-load OAuth correctness (**item #31**): assert no stdio server ever attempts the OAuth spec flow
  (env/mcp-remote only); HTTP-oauth servers complete discovery→DCR→PKCE→token with the `resource` param
  and authorize cleanly; refresh works; expired-refresh surfaces `needs_reauth` (`views.py:353/384`).
- The repo's SSRF re-guard on attacker-controlled metadata endpoints (`oauth.py:309`,
  `mcp_oauth_proxy._assert_safe_url`) exceeds the spec and directly addresses the spec's "Open
  Redirection" + metadata-SSRF security considerations — do not weaken.

Sources: [MCP Authorization 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization),
[RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728), [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html),
[RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414), [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591).

Companion: `oss-research-oauth-sandbox-client.md` (P2.7) — in-sandbox OAuth client implications for P3/P4.
