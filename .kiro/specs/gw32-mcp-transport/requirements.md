# Requirements Document

## Introduction

This spec covers three runbook cards that share the MCP surface in `gateway_v2`:

- **GW32** (MCP parity beyond the call path). Depends on GW17 and GW17b. Required by GW21 and GW23. Objective: keep MCP clients working unchanged at cutover.
- **GW17b** (authenticated MCP upstreams). Depends on GW17. Required by GW21 and GW23. Objective: serve MCP servers that need OAuth, a bearer token, basic auth or a custom header, which v2 refuses today with `mcp_upstream_auth_unsupported` (403).
- **GW17c** (per-key MCP tool controls). Depends on GW17 and GW05. Required by GW21 and GW23. Objective: restore v1's per-key `mcp_allowed_tools` and `mcp_max_tool_calls` on the v2 MCP surface.

Build order is GW17b, then GW32, then GW17c, because GW32 depends on GW17b (the OAuth setup routes and the credential path), and GW17c depends only on GW17 and GW05. Requirement 1 (identity before `initialize`) is independent of all three and is built first, because it is a security defect in shipped code.

Sources read for this spec: the v3.1 runbook (GW32 card, V-17, GW17 review block, GW14b admin-ledger note, owner decision 11), the backend rewrite runbook (GW17b and GW17c cards, section 0-A.4/0-A.5), the v3.1 progress file (cards not started, "MCP `initialize`/`ping` answer before identity (GW32)"), and the shipped GW17 spec `.kiro/specs/mcp-surface-gw17`.

### What exists today (verified in this tree)

- `gateway_v2/gateway_v2/edge/mcp.py` (421 lines) serves `POST /v1/mcp/{server_slug}` as JSON only. In `McpRoute._serve`, `initialize`, `ping` and `notifications/initialized` are answered by `_local()` before `_tenant()` runs, so they answer with no identity. This is the defect GW32 must fix.
- `edge/app.py` declines every ASGI scope that is not `http` or `lifespan` (no WebSocket). `edge/routes.py` is 799 lines (cap 800).
- `edge/mcp_registry.py` marks any server with `auth_type != "none"` or `oauth_authorized` as `needs_auth`, and `McpRoute._registry_gate` refuses it. Control's server list never returns credentials, by design.
- `dispatch/mcp_broker.py` posts `SandboxRpcRequest` to `{broker}/v1/sandbox/{slug}/rpc`. The broker's `UpstreamConfig` already has a `headers` field, and all four transports (stdio, streamable-http, sse, websocket) already route through the per-org sandbox.
- `Principal` (`domain/identity.py`) has `key_id`, `org_id`, `rate_per_s`, `burst`, `feed_seq`. `admit/identity.py:principal_of` parses a signed KEY record. `state_control/projector.py:_key_body` writes only those four fields. `state_control/source.py:SourceKey` carries `key_hash`, `key_id`, `org_slug`, `active`.
- Control already has `GatewayAPIKey.mcp_allowed_tools` (JSON list, empty means all) and `mcp_max_tool_calls` (integer, 0 means unlimited), and `MCPServerRegistration` has `auth_type`, an encrypted `auth_token`, `oauth_*` fields (including an encrypted refresh token and `oauth_token_expires_at`) and `needs_reauth`. Control has `internal/needs-reauth/`.
- v1 serves `POST /gateway/{org}/mcp/{server}` (streamable HTTP with sessions), the OAuth 2.1 server routes (`/.well-known/oauth-protected-resource[/...]`, `/.well-known/oauth-authorization-server`, `/oauth/register`, `/oauth/authorize` GET and POST, `/oauth/token`), and the upstream OAuth setup routes (`/gateway/{org}/mcp/{server}/oauth/start`, `/gateway/oauth/callback`, `/gateway/{org}/mcp/{server}/oauth/status`). v1 keeps OAuth clients and auth codes in module-level dicts, and its token endpoint returns the raw gateway API key as the access token.
- `runtime/resources.py` is exactly 800 lines, the `lint/check_sizes.py` cap. `lint/check_capacity_literals.py` allows capacity literals only in `runtime/resources.py` (`ALLOWED_REL`).

### Decisions recorded for this spec

These resolve open questions in a documented, conservative, fail-closed way. None of them blocks the build. The human may overturn any of them in review.

- **D-1 MCP client URL (runbook owner decision 11).** Keep the v1 path `/gateway/{org}/mcp/{srv}` as the working default. This spec serves it directly. It does not ship a redirect plan, and no dated client-migration notice is issued. `/v1/mcp/{slug}` stays served, as a second path to the same handler. This is recorded as a working default, not an owner signature.
- **D-2 Org in the path.** The path carries an org slug for v1 URL compatibility. The org for every decision still comes from the resolved key only. If the path org does not match the key's org, the request is refused. The path org is never trusted.
- **D-3 OAuth access token.** The gateway as an OAuth 2.1 server issues an opaque, gateway-minted access token bound to the key and org. It does not return the raw API key as v1 does. Reason: returning the key puts a long-lived secret into token responses, proxies and client logs, and breaks revocation semantics. Cost: the identity path must accept the token; see Requirement 7.
- **D-4 Upstream credential custody.** Control stays the system of record for upstream credentials (encrypted at rest, as it is today). The gateway never persists a plaintext credential. The credential reaches the broker per request, over the internal channel, as `upstream.headers`. See Requirements 11 to 14.
- **D-5 Resource-literal location.** `runtime/resources.py` is at the 800-line cap and cannot take members. New capacity literals go in a new module `runtime/resources_mcp.py`, and the capacity-literal gate is extended to an allowlist of exactly two paths, with a positive and a negative gate test. This is the same approach the GW27 design decided (D-13: `ALLOWED_RELS` frozenset, `resources_org.py`). If GW27 lands first, the allowlist is a union of both modules. This spec does not edit `resources.py`.
- **D-6 Cap window.** The per-key tool-call cap keeps v1's meaning: calls per rolling-or-fixed 60 second window, counted per key. The window length is a contract-derived bound, not a literal at the call site.
- **D-7 "Real servers".** "HTTP+SSE and WebSocket upstreams against real servers" is split. Offline, the transports are proven against real loopback servers speaking the protocol (a real socket, not a fake object). Against a real sandbox and a real third-party MCP server, it is a deferred live item.

### Scope boundaries

- **In scope.** The ASGI MCP client transport (streamable HTTP with sessions, GET stream, DELETE, SSE replies, WebSocket), the v1 path, identity-first handling of every method, the OAuth 2.1 server for MCP clients, the upstream OAuth setup routes, the upstream credential path from control to broker, `Principal` MCP fields end to end (control source, projector, signed record, `principal_of`), per-key enforcement, the off-path call counter, new value-codes, audit and console changes for these paths, and all offline tests and gates.
- **Reuse, do not rebuild.** The GW17 pipeline (`_tenant`, admission, budget, scan, `stage_upstream`, `enforce_reply`), `RequestHolds`, `McpRegistry`, `BrokerClient`, `BudgetLease` idiom, `IdentityCache`, `Error_Envelope`, the audit callback, the `{rv2}` store-key helpers.
- **Out of scope.** Listed in the final section.

### Deferred live gates (human items)

The unattended run stops before these. They are not stubbed as local passes, and no requirement below may be satisfied by claiming them.

- **DL-1 (L32 URL).** A stock MCP client (VS Code or the MCP SDK client) connects to `/gateway/{org}/mcp/{srv}` on the Docker stack with only the key changed, and runs initialize, tools/list and tools/call.
- **DL-2 (L32 OAuth).** A real MCP client completes discovery, dynamic registration, authorize with PKCE and token exchange against the running gateway, then calls a tool.
- **DL-3 (L32 upstreams).** A real HTTP+SSE MCP server and a real WebSocket MCP server are reached through the real broker and sandbox, with `tools/list` and `tools/call` succeeding and the sandbox host allowlist and SSRF check observed.
- **DL-4 (L17b-1, -2, -3, -4 live).** A real token server and a real OAuth server (with real refresh) through control, broker and sandbox, with the secret absent from every real response, container log and audit record. Revocation within the registry TTL on the live stack. Two real orgs on one stack.
- **DL-5 (L17c live).** The key edit in the console propagates through the real change feed within the identity freshness bound, and the cap holds across real workers on a real Valkey.
- **DL-6.** The GW26 MCP live claims that this card unblocks. They are the human's to run after the live gate.
- **DL-7.** The C2 MCP replay (LGW17-7) and the fleet-scale LGW17-4 stay deferred as in GW17.

## Glossary

- **MCP_Surface**: The handlers in `edge/mcp.py` and the new edge modules that serve MCP to clients.
- **V1_Path**: `/gateway/{org}/mcp/{srv}` (and `/gateway/{org}/mcp/{srv}/` with a trailing slash).
- **Client_Transport**: One of streamable HTTP (POST, GET, DELETE), SSE reply framing on POST, and WebSocket.
- **MCP_Session**: A server-side session identified by `Mcp-Session-Id`, created by `initialize`, bound to one key and one org and one server slug.
- **OAuth_Server**: The gateway's OAuth 2.1 authorization server for MCP clients.
- **MCP_Access_Token**: The opaque token the OAuth_Server issues. Not the API key.
- **Upstream_Credential**: A secret control holds for a registered MCP server: bearer token, basic username and password, custom header value, or OAuth access and refresh tokens and client secret.
- **Credential_Handoff**: The one path by which an Upstream_Credential moves from control to the broker request.
- **Credential_Material**: Any Upstream_Credential value, any value derived from it that can reconstruct it (base64 of basic auth, a header value), and any MCP_Access_Token.
- **needs_reauth**: The state in which an OAuth upstream has no usable token and cannot refresh.
- **Tool_Policy**: The pair `mcp_allowed_tools` (allow-list, empty means unrestricted) and `mcp_max_tool_calls` (per-window cap, 0 means unlimited) carried on a Principal.
- **Call_Counter**: The per-key tool-call count, kept off the request path.
- **Identity_Freshness_Bound**: The bound within which a published key change takes effect on the gateway (the existing `FRESH_MS` / feed-lag bound; the identity cache is valid "as of a known feed position").
- **FAIL_OPEN**: Forwarding a request, returning a result, or granting access when a check cannot be computed. Forbidden. The `FAIL_OPEN` counter stays 0.

## Requirements

### Requirement 1: Identity before any MCP method

**User Story:** As a security engineer, I want `initialize`, `ping` and every notification to require a valid key, so that an unauthenticated caller learns nothing about the gateway and cannot create state.

#### Acceptance Criteria

1. WHEN a request reaches any MCP route (V1_Path, `/v1/mcp/{slug}`, any GET, DELETE, or WebSocket upgrade) THE MCP_Surface SHALL resolve identity before it answers any method, including `initialize`, `ping` and `notifications/initialized`.
2. IF identity does not resolve THEN THE MCP_Surface SHALL return the declared identity refusal (`invalid_api_key` as 401 for an unknown or missing key; `shared_state_unavailable` as 503 when state is down), SHALL create no MCP_Session, and SHALL make zero registry and zero broker calls.
3. THE MCP_Surface SHALL NOT disclose server info, protocol version, capabilities, or whether a server slug exists to an unauthenticated caller.
4. WHEN identity resolves, `initialize` and `ping` SHALL still be answered locally (not forwarded), as in GW17.
5. A malformed JSON-RPC body from an unauthenticated caller SHALL be answered 401, not a JSON-RPC parse error. This closes the "400 before 401" item recorded under GW36 for the MCP routes. Body reading SHALL stay bounded before identity resolves.
6. A regression test SHALL fail if `_local` or any equivalent path can answer before identity (a negative control that re-orders the code must turn the test red).

### Requirement 2: The v1 client URL

**User Story:** As an operator with existing MCP clients, I want `/gateway/{org}/mcp/{srv}` to keep working, so that cutover does not break every client configuration.

#### Acceptance Criteria

1. THE MCP_Surface SHALL serve the V1_Path with the same handler, the same lifecycle and the same pipeline as `/v1/mcp/{slug}` (per D-1). `/v1/mcp/{slug}` SHALL keep working.
2. FOR the V1_Path THE MCP_Surface SHALL take the org from the resolved key only (D-2). IF the path `{org}` is not the key's org (by the declared org map: v2 org id to control slug) THEN it SHALL refuse with `mcp_org_mismatch` (403) and SHALL make zero registry and zero broker calls. The response SHALL NOT reveal whether the other org or server exists.
3. THE path parser SHALL reject an empty org, an empty server, an extra path segment, a `/` or NUL inside a segment, and percent-encoded separators, with a declared not-found, and SHALL make zero calls.
4. THE V1_Path SHALL NOT shadow the v1 bare REST routes (`/gateway/{org}/mcp/{srv}/tools`, `/tools/call`, `/health`) or the v1 OAuth setup routes. Those paths SHALL keep their declared behaviour (404/405/501 as the route table defines), and their GW14b admin-ledger rows SHALL NOT change except where Requirement 9 changes the `oauth/start` row.
5. THE decision SHALL be recorded in the runbook note for owner decision 11 as "kept `/gateway/{org}/mcp/{srv}` (working default); no redirect shipped; no migration notice", without marking the decision as owner-signed.
6. A route-table test SHALL pin every MCP path and method the card serves and every path it must not serve.

### Requirement 3: Streamable HTTP with sessions

**User Story:** As an MCP client developer, I want the streamable HTTP transport (session header, GET stream, DELETE) so that stock clients connect unchanged.

#### Acceptance Criteria

1. WHEN identity resolves and `initialize` succeeds THE MCP_Surface SHALL create an MCP_Session, return `Mcp-Session-Id` (a cryptographically random, unguessable, visible-ASCII value), and bind the session to the key id, org id and server slug.
2. WHEN a later request carries `Mcp-Session-Id` THE MCP_Surface SHALL accept it only if it is live and bound to the same key id, org id and server slug as the resolved identity. IF the session is unknown, expired, or bound to anything else THEN it SHALL return 404 `mcp_session_invalid`. IF the key was revoked THEN the session SHALL be unusable within the Identity_Freshness_Bound (identity is resolved on every request, and the session is never a substitute for it).
3. A request without `Mcp-Session-Id` SHALL still work for every method except where the MCP spec requires a session. The session is not a security boundary: every request is authenticated and scanned on its own.
4. WHEN a client sends `DELETE` on the MCP path with a live session bound to its identity THEN THE MCP_Surface SHALL end the session and return 204. DELETE with an invalid session SHALL return 404. DELETE without identity SHALL return 401.
5. WHEN a client sends `GET` with `Accept: text/event-stream` and a live session THEN THE MCP_Surface SHALL open an SSE stream (see Requirement 4). IF the server has no server-initiated messages to send THEN the stream SHALL stay open, send comment keepalives at an interval from the ResourceContract, and close at the session or stream lifetime bound. GET without `text/event-stream` SHALL return 405 with `Allow`.
6. THE MCP_Surface SHALL validate the `Origin` header when one is present: an `Origin` that is not in the declared allow set (empty set by default means "reject any Origin that is present and not equal to the request host") SHALL be refused with `mcp_origin_refused` (403) before identity-bound state is touched. This is the DNS-rebinding defence the MCP spec requires. A request with no `Origin` header is not refused for that reason.
7. THE MCP_Surface SHALL read `MCP-Protocol-Version` when present, accept the declared supported set, and refuse an unsupported value with a declared 400. A missing header SHALL be treated as the default version.
8. THE session store SHALL be bounded (max sessions per key, per org, and global; idle TTL; absolute lifetime), with every bound from the ResourceContract. WHEN a bound is hit THE MCP_Surface SHALL refuse the new `initialize` with a declared 429 or 503 and SHALL NOT evict a live session of another key.
9. THE session store SHALL hold no module-level mutable state. It SHALL be an injected object owned by the app. Session ids SHALL be stored hashed.
10. Session creation, use and end SHALL leave audit records (route `mcp`, event `session_open` / `session_close`) without the session id in clear text.

### Requirement 4: SSE reply framing and server-initiated messages

**User Story:** As an MCP client developer, I want SSE replies where the client asks for them, so that clients that expect `text/event-stream` work.

#### Acceptance Criteria

1. WHEN a POST carries `Accept` including `text/event-stream` and the handler's result is a JSON-RPC reply THEN THE MCP_Surface SHALL be allowed to answer with `Content-Type: text/event-stream` and exactly one `message` event carrying the same JSON-RPC body, followed by stream close. WHEN `Accept` lists only `application/json` THEN it SHALL answer JSON. WHEN `Accept` is absent or `*/*` THEN it SHALL answer JSON.
2. THE scan and the decision SHALL be identical for both framings. A reply SHALL be fully scanned, decided and (if needed) masked before the first byte of either framing is sent. No byte of an unscanned upstream result SHALL reach the client.
3. THE SSE encoder SHALL reuse the shipped `edge/wire/sse.py` framing (`SSEEncoder`), SHALL escape every newline in event data so a payload cannot inject an event, and SHALL set `Cache-Control: no-store` and `X-Accel-Buffering: no`.
4. A server-initiated request or notification from an upstream (`sampling/createMessage`, elicitation, `notifications/progress`) SHALL stay scanned and withheld as GW17 requires. This spec does not relay them. The SSE interleave of progress stays recorded as untestable through the broker's one-envelope reply (GW17 design change 2).
5. THE stream SHALL stop writing within the contract's cancellation bound when the client disconnects, SHALL release the admission slot and any hold exactly once, and SHALL NOT leave a task running.
6. A stream SHALL close at the declared lifetime bound with a final event or a clean close, never a hang.

### Requirement 5: WebSocket client transport

**User Story:** As an MCP client developer, I want a WebSocket endpoint for MCP, so that clients that use that transport work.

#### Acceptance Criteria

1. THE app SHALL handle ASGI `websocket` scopes for the declared MCP WebSocket path (the V1_Path with the WebSocket upgrade, and `/v1/mcp/{slug}`), and SHALL still decline every other websocket path with a clean close (no protocol error, no state change).
2. THE MCP_Surface SHALL resolve identity from the upgrade request headers (`Authorization: Bearer`, MCP_Access_Token, or the same key header the HTTP surface accepts) before it sends `websocket.accept`. IF identity does not resolve THEN it SHALL close without accepting, using close code 1008 or an HTTP 401/403 on the handshake as ASGI allows, and SHALL create no session and make zero registry and broker calls. It SHALL NOT accept a key from the query string.
3. THE `Origin` rule in Requirement 3.6 SHALL apply to the upgrade.
4. EACH text frame SHALL be one JSON-RPC message handled by the same pipeline as an HTTP POST, including the per-message tool policy check (Requirement 18) and the scan. A binary frame, a frame over the body bound, or a batch SHALL be refused with a declared close or a JSON-RPC error and SHALL NOT be forwarded.
5. THE connection SHALL re-check identity at an interval from the ResourceContract no longer than the Identity_Freshness_Bound, and SHALL close with 1008 when the key is revoked or the org is disabled. Messages in flight when the key is revoked SHALL NOT start a broker call after revocation is observed.
6. THE connection SHALL be bounded: max connections per key, per org and global, max messages in flight per connection (default 1, strictly ordered replies), idle timeout, absolute lifetime, max frame size, and a send backpressure bound. Each bound comes from the ResourceContract. A slow reader SHALL be closed at the bound, never buffered without limit.
7. THE connection SHALL hold its admission slot, and charge budget per message as an HTTP request would. WHEN a hold is released THEN it SHALL be released exactly once, including on disconnect mid-message.
8. WHEN the client closes THEN the MCP_Surface SHALL cancel any broker call in progress within the cancellation bound.
9. THE WebSocket handler SHALL use raw ASGI (no new framework in `gateway_v2`). It SHALL import no HTTP or WebSocket framework outside `edge`.

### Requirement 6: Gateway as an OAuth 2.1 server for MCP clients

**User Story:** As a user of an OAuth-only MCP client, I want the gateway to expose the OAuth endpoints MCP clients discover, so that I can connect without hand-copying a key into a config.

#### Acceptance Criteria

1. THE OAuth_Server SHALL serve, unauthenticated by design: `GET /.well-known/oauth-protected-resource`, `GET /.well-known/oauth-protected-resource/{path}`, `GET /.well-known/oauth-authorization-server`, `POST /oauth/register`, `GET /oauth/authorize`, `POST /oauth/authorize`, `POST /oauth/token`. Each other method on these paths SHALL return 405 with `Allow`.
2. THE discovery documents SHALL be built from one configured public base URL (`GATEWAY_PUBLIC_URL` equivalent), validated at start (https, or http only for loopback), never from the request `Host` or `X-Forwarded-*` headers. IF the public URL is unset in a production profile THEN the OAuth routes SHALL return a declared 503, and discovery SHALL NOT advertise a guessed URL.
3. THE authorization server metadata SHALL advertise only what is implemented: `code` response type, `authorization_code` and `refresh_token` grants only if refresh is implemented, `S256` as the only PKCE method, `none` and `client_secret_post` token endpoint auth methods as implemented, and the registration endpoint.
4. `POST /oauth/register` SHALL accept RFC 7591 dynamic client registration. It SHALL require at least one `redirect_uri`, SHALL accept a redirect URI only if it is https, loopback http (`127.0.0.1`, `[::1]`, `localhost`), or a declared allowed custom scheme (`vscode`, `vscode-insiders`, `cursor`), SHALL refuse a redirect URI with a fragment, userinfo, or wildcard, SHALL bound the number of clients per source and in total, and SHALL return `client_id` (and a secret only when a confidential method is requested). The request body SHALL be size-bounded.
5. `GET /oauth/authorize` SHALL require `response_type=code`, a registered `client_id`, a `redirect_uri` that EXACTLY equals a registered one, a `code_challenge` and `code_challenge_method=S256`. PKCE SHALL be mandatory. ANY validation failure on `client_id` or `redirect_uri` SHALL render a local error page and SHALL NOT redirect. Any other error SHALL redirect with `error` and `state` per RFC 6749 only after `redirect_uri` has been validated.
6. THE authorization step SHALL authenticate the user with a gateway API key submitted in `POST /oauth/authorize` (v1 parity), over the CSRF-protected form (a single-use, session-bound form token). The key SHALL be resolved through the same identity resolver as chat. IF it does not resolve THEN the response SHALL be a generic invalid-credentials error with identical timing class and text for unknown and malformed keys. Repeated failures SHALL be rate limited per source, with the bound from the ResourceContract.
7. THE authorization code SHALL be random with at least 128 bits, single use, bound to client id, redirect URI, code challenge, and key id, and expire at a TTL from the ResourceContract (no more than 10 minutes). THE code SHALL NOT contain or encode the API key. Presenting a code twice SHALL fail and SHALL revoke the tokens that code produced.
8. `POST /oauth/token` SHALL verify the code, client id, exact redirect URI and the S256 verifier, then issue an MCP_Access_Token (D-3). The token SHALL never equal or embed the API key. The response SHALL carry `Cache-Control: no-store` and `Pragma: no-cache`, `token_type: Bearer`, `expires_in`, and the granted scope. THE token lifetime SHALL come from the ResourceContract.
9. IF refresh tokens are issued THEN they SHALL be rotated on each use, single use, bound to client and key, and a reuse of a rotated refresh token SHALL revoke the whole token family.
10. THE OAuth_Server SHALL NOT use the implicit grant, the password grant, or any flow that returns a token in a URL.
11. ALL OAuth state (clients, codes, tokens, form tokens, failure counters) SHALL live in an injected store object (Valkey-backed in production, in-memory twin in tests, per the existing `store_keys` `{rv2}` hash-tag idiom), with TTLs on everything. There SHALL be no module-level mutable state. Tokens and codes SHALL be stored only as a keyed hash. A store outage SHALL fail closed (declared 503 on OAuth routes; MCP routes then refuse a token with `shared_state_unavailable`).
12. THE OAuth routes SHALL emit audit records (`oauth_register`, `oauth_authorize_ok`, `oauth_authorize_denied`, `oauth_token_issued`, `oauth_token_refused`) without any code, token, key, verifier or secret in them. Failed authorizations SHALL be audited with a source key (hash) and the declared reason code.
13. ERROR bodies SHALL be RFC 6749 JSON (`error`, `error_description` from a fixed table). They SHALL NOT echo input, exception text or a stack trace.
14. THE OAuth routes SHALL be bounded in body size and time, and SHALL be covered by the edge's existing unknown-path and method rules (no 501 placeholder for them).

### Requirement 7: MCP clients authenticate with an MCP_Access_Token

**User Story:** As an MCP client that obtained a token from the OAuth_Server, I want to use it on the MCP routes, so that the OAuth flow ends in a working connection.

#### Acceptance Criteria

1. THE identity step for MCP routes SHALL accept `Authorization: Bearer <token>` where the token is either a gateway API key or a live MCP_Access_Token. A token SHALL be accepted only on MCP routes (HTTP and WebSocket), never on chat, embeddings, responses or any other surface; there it is an invalid key.
2. A MCP_Access_Token SHALL resolve to the same Principal as the key it was issued for, as resolved now (not as cached in the token). Revoking or deactivating the key SHALL make the token unusable within the Identity_Freshness_Bound. The token SHALL carry no org or key claim the gateway trusts without resolving.
3. AN expired, revoked, malformed, or unknown token SHALL be a 401 with `WWW-Authenticate: Bearer resource_metadata="<public base>/.well-known/oauth-protected-resource"` (RFC 9728) so a client can start discovery. A 401 for a key (non-token) SHALL carry the same header on MCP routes.
4. THE identity resolver change SHALL not weaken the existing path: the chat 401/503 behaviour, timing, and metrics SHALL be unchanged (chat regression tests stay green).
5. A token SHALL NOT be logged, audited or put in an error. The audit record for an authenticated request SHALL carry the key id, not the token.

### Requirement 8: Upstream HTTP+SSE and WebSocket transports against real servers

**User Story:** As an operator, I want proof that the SSE and WebSocket upstream transports work against real protocol servers, not only against fakes.

#### Acceptance Criteria

1. THE repository SHALL contain a test-only real MCP server fixture for each of HTTP+SSE and WebSocket, speaking the protocol on a real socket on loopback (asyncio streams or the `websockets` library if present; the test `importorskip`s a missing optional library, and the base package stays dependency-free).
2. THE offline tests SHALL drive the broker request the MCP_Surface builds (transport name, `upstream.url`, `allowed_hosts`, `headers`, `oauth_client_role`) for `sse` and `websocket` servers into those real fixtures through a protocol-faithful stand-in for the sandbox agent that opens a real connection, and SHALL assert `initialize`, `tools/list` and `tools/call` round trips, a mid-stream disconnect, an oversize frame, a server that never answers (timeout), and a server that answers with a JSON-RPC error.
3. THE transport value sent to the broker for each registered transport SHALL come from one declared table (`streamable-http`, `sse`, `websocket`, `stdio`). An unknown transport SHALL be refused with a declared code and zero broker calls (not coerced to a default).
4. THE gateway-side destination check (GW17 Requirement 6.4) SHALL cover `ws://` and `wss://` and `http(s)://` hosts, and SHALL refuse a `ws://` URL to a non-loopback host unless the declared policy allows plaintext (default: refuse non-TLS for remote hosts, fail closed).
5. THE offline fixtures SHALL use only loopback and SHALL NOT weaken the SSRF refusal in production code paths (the loopback permission, if any, is a test-injected resolver or allow-host, never a production default).
6. THE tests SHALL NOT claim DL-3. They SHALL state in a header comment that the real broker and sandbox run is deferred.

### Requirement 9: MCP OAuth setup routes (upstream OAuth)

**User Story:** As a console user, I want to authorize an OAuth upstream from the console, so that the gateway can call it.

#### Acceptance Criteria

1. THE gateway SHALL serve the v1-compatible setup routes: `POST /gateway/{org}/mcp/{srv}/oauth/start`, `GET /gateway/oauth/callback`, `GET /gateway/{org}/mcp/{srv}/oauth/status`, each authenticated as a gateway API key of the path org (the org check of Requirement 2.2 applies).
2. THE authorization flow SHALL be handled in one place. Control remains the owner of the stored result (Requirement 11). Where the flow runs in the gateway, the gateway SHALL deliver the obtained tokens to control through the credential channel (Requirement 11.4) and SHALL NOT keep them.
3. `oauth/start` SHALL use PKCE S256, a random single-use `state` bound to key id, org and server slug with a short TTL from the ResourceContract, and SHALL refuse `server_url` values failing the gateway-side destination check (Requirement 8.4 / GW17 6.4) for discovery, registration, authorization and token endpoints, including endpoints learned from remote metadata.
4. `oauth/callback` SHALL reject unknown, expired, replayed or mismatched `state`, SHALL render only escaped, fixed-text pages, and SHALL NOT echo `error_description` unescaped or any token.
5. `oauth/status` SHALL return `authorized`, `expired`, `refreshable`, `needs_reauth` and `obtained_at` only. It SHALL NOT return a token, a client secret, a verifier or a token endpoint URL with credentials.
6. THE admin-ledger row for MCP `oauth/start` SHALL be moved from `retire` to `build` with this card named as the build owner (the GW14b note says it needs "the signed decision this card requires, or a build owner (GW32, GW35)"). The ledger check SHALL stay green. IF the change would need a signature the unattended run cannot give THEN the row SHALL stay `retire` and the requirement SHALL be recorded as DEFERRED to the human with the reason, not silently skipped.
7. THE setup routes SHALL be bounded (body, time, redirects: none followed to another host without re-running the destination check).

### Requirement 10: GW17b security design is a deliverable

**User Story:** As a security reviewer, I want a written threat model for the credential path before I trust it, because the runbook says this card needs its own security design.

#### Acceptance Criteria

1. THE design phase SHALL include a "GW17b security design" section that names, for each Upstream_Credential type, where it is stored, who can read it, how it travels (control to gateway or broker), where it is held in memory and for how long, and what zeroes or drops it.
2. THE section SHALL list the threats considered and the control for each, at least: secret in list responses; secret in gateway, control, broker and sandbox logs; secret in audit records and console objects; secret in exception text and error envelopes; secret in the pipeline trace and Scan Detail; secret in metrics labels; secret in the scan path (the secret is not input text and SHALL NOT be scanned, stored or masked as such); cross-org credential use; confused deputy through a registry cache keyed by slug only; SSRF through an attacker-chosen upstream URL that receives the credential; redirect that forwards the credential to another host; downgrade to http; a revoked credential still cached; refresh-token theft and rotation; replay of a refresh; credential in a core dump or in `repr()` of a value type.
3. THE section SHALL state the residual risks (the sandbox holes inherited from GW17 stay open) and SHALL NOT claim a property the tests do not show.

### Requirement 11: Credential handoff from control, never in a list

**User Story:** As an operator with an authenticated MCP server, I want the gateway to call it with the stored secret, with the secret appearing nowhere it should not.

#### Acceptance Criteria

1. THE existing list endpoint (`GET /api/mcp-connector/servers/`) SHALL keep returning no credential. A test SHALL fail if any Upstream_Credential field name or value appears in a list response.
2. THE gateway SHALL fetch a server's credential through a separate internal endpoint, authenticated with the gateway internal key, scoped to one org and one server slug per call, never a bulk endpoint, and never exposed on a console-facing route. The control side is built as part of this card (the endpoint is new; the encrypted storage and `needs_reauth` field already exist).
3. THE endpoint SHALL return a typed credential: `none`, `bearer`, `basic`, `header`, or `oauth`. For `oauth` it returns the current access token and its expiry and whether it is refreshable, and SHALL NOT return the refresh token or client secret unless the gateway-side refresh design (Requirement 13) decides the gateway refreshes; if it does, the refresh token SHALL travel only on that endpoint.
4. WHERE the gateway obtains OAuth tokens (Requirement 9) or refreshes them, THE gateway SHALL write them back to control through a separate internal write endpoint, encrypted at rest by control, using the same internal key. The write SHALL be idempotent per (org, server, token version).
5. THE internal endpoints SHALL require the internal key, SHALL compare it in constant time, SHALL require the org slug the gateway derived from the key's org map (never client-supplied), SHALL refuse an org/server pair the key's org does not own with a response indistinguishable from "no such server", and SHALL be rate limited.
6. THE transport between control and gateway for these endpoints SHALL be the internal network only. The gateway SHALL refuse to send a credential request to a control URL that is not https unless the host is loopback or declared internal-plaintext in config, and SHALL refuse to start in a production profile with an unsafe setting.
7. THE gateway SHALL receive a credential only when it is about to use it (a `tools/call`, `tools/list`, or other forwarded method for that server), and SHALL NOT prefetch credentials for all servers.
8. THE `ServerEntry` and `McpServer` value types SHALL NOT gain a field that holds a credential. The credential SHALL be carried in a separate value type with a `__repr__`, `__str__`, `__reduce__` and pickling behaviour that never print or serialise the secret (it prints a fixed `<redacted>` token), frozen and slotted.
9. THE legacy refusal `mcp_upstream_auth_unsupported` SHALL remain only for an `auth_type` the gateway does not implement (unknown values), refused fail-closed. Known types SHALL NOT be refused for being authenticated.

### Requirement 12: Credential types and their wire form to the broker

**User Story:** As an operator, I want each authentication style to produce the header the upstream expects.

#### Acceptance Criteria

1. THE gateway SHALL support these types, rendered into the broker request's `upstream.headers` only (never into the URL, query string, `params`, `env` or the JSON-RPC body):
   - `bearer`: `Authorization: Bearer <token>`
   - `basic`: `Authorization: Basic <base64(user:password)>`
   - `header`: `<configured name>: <value>`
   - `oauth`: `Authorization: Bearer <access token>` (the token type from the token response is honoured only for `Bearer`; any other type is refused with a declared code)
2. THE custom header name SHALL be validated against an allowed pattern (RFC 7230 token), a bound length, and a deny list (`Host`, `Content-Length`, `Content-Type`, `Transfer-Encoding`, `Connection`, `Upgrade`, `X-Request-ID`, `X-Broker-Key` and the broker's own auth header, `Cookie`, hop-by-hop headers). An invalid header name or a value containing CR, LF, NUL, or non-printable bytes SHALL be refused with `mcp_upstream_credential_invalid` and zero broker calls (header injection).
3. THE credential SHALL be sent only on a destination that passed the gateway-side destination check for the registered URL, and SHALL be sent only to the broker (never directly to the upstream, per GW17 Requirement 6.1).
4. FOR a remote transport THE credential SHALL be sent only when `upstream.allowed_hosts` contains exactly the registered URL's host, and the `oauth_client_role` stays `forbidden_in_sandbox`.
5. THE gateway SHALL refuse to attach a credential to a plaintext `http://` or `ws://` upstream on a non-loopback host (declared code `mcp_upstream_insecure_transport`), unless an explicit per-deployment setting allows it. The default is refuse.
6. A stdio server SHALL NOT receive a credential in `env` through this path unless a declared per-server mapping says so. Default: a credential on a stdio server is refused with `mcp_upstream_credential_invalid`. (v1's `env_vars` handling is unchanged and out of scope.)
7. THE `auth_type none` path SHALL behave exactly as GW17: no credential call to control, no extra latency, same broker body byte for byte.
8. THE secret SHALL be absent from the gateway's own scan path: it SHALL NOT be passed to the detector, the redactor, the pipeline trace, the console object, `Finding.evidence` or any audit detail. The broker reply SHALL still be scanned in full, and a reply that contains the credential value SHALL be treated by the existing output scan, with an added exact-match check: IF the credential value (or its base64 form) appears in the upstream reply or the error THEN it SHALL be masked or the reply withheld (declared `output_blocked`), so a reflecting upstream cannot return the secret to the client.

### Requirement 13: Refresh and needs_reauth

**User Story:** As an operator, I want an expired OAuth token to refresh once, and a failed refresh to refuse clearly, never to forward without credentials.

#### Acceptance Criteria

1. WHEN an OAuth credential is expired or within a skew bound of expiry (bound from the ResourceContract) THEN THE gateway SHALL refresh it before forwarding. Concurrent requests for the same (org, server) SHALL share one refresh (single flight). A test SHALL show N concurrent requests produce exactly one refresh call.
2. A refresh SHALL call the token endpoint recorded for that server, only after that endpoint passed the destination check (Requirement 8.4), with a timeout and a response size bound from the ResourceContract, and with no redirect followed across hosts.
3. WHEN refresh succeeds THEN the new token SHALL be written back to control (Requirement 11.4) and used for the waiting requests. IF the write-back fails THEN the request MAY use the new token once, the refresh SHALL be logged at WARNING without the token, and the next request SHALL refresh again rather than trust an unwritten token. A rotated refresh token that cannot be persisted SHALL cause `needs_reauth` handling rather than a silent loss.
4. WHEN refresh fails (HTTP error, `invalid_grant`, malformed response, timeout, or no refresh token) THEN THE MCP_Surface SHALL return the declared `mcp_upstream_needs_reauth` code (HTTP 401 or 403 as the code table declares; JSON-RPC error with that `error.data.code`), SHALL make zero broker calls for that request, and SHALL mark `needs_reauth` in control through `internal/needs-reauth/`. It SHALL NEVER forward the request without the credential. A transient failure (timeout, 5xx) SHALL use a distinct declared code `mcp_upstream_credential_unavailable` (503) and SHALL NOT set `needs_reauth` on the first failure; the number of failures before it does comes from the ResourceContract.
5. WHILE a server is in `needs_reauth` THE MCP_Surface SHALL refuse every forwarded method for it with `mcp_upstream_needs_reauth` and zero broker calls, until control clears the state or a fresh token is stored. The state SHALL be visible to `oauth/status`.
6. THE refresh SHALL NOT be attempted on the request path more than once per request.
7. THE refresh path SHALL never spend more than the declared total budget of time in a single request (a bound from the ResourceContract), and SHALL release admission holds exactly once on every exit.

### Requirement 14: Credential secrecy and revocation

**User Story:** As a security reviewer, I want measurable guarantees that no secret leaks and that a revoked credential stops being used.

#### Acceptance Criteria

1. FOR every test that serves a `tools/call` through a token server, a basic server, a header server and an OAuth server THE harness SHALL capture all of: the HTTP response bodies and headers returned to the client, every log record emitted (a handler attached to the root logger and to `gateway_v2`), every audit record, the console object and pipeline trace, the metrics exposition, and the exception text of every injected failure, and SHALL assert the Credential_Material (plain, base64, URL-encoded, and a 6-character prefix) appears in none of them (L17b-1 offline form).
2. A negative control SHALL show the harness catches a deliberately leaking variant (a test-only patch that logs the header turns the test red).
3. THE credential cache in the gateway SHALL be per worker RAM only, keyed by (org, server slug), with a TTL no longer than the registry TTL (`registry_ttl_s`), SHALL drop an entry when the `mcp:scan_ver` poll shows a change for that org or server, and SHALL drop the entry on a `needs_reauth` mark. A credential revoked or changed in control SHALL stop being forwarded within the registry TTL (L17b-3 offline form: with an injected clock, a call at TTL minus one tick may use the old value, and a call at TTL plus one tick fetches again and sees the revocation, then refuses with no broker call).
4. THE cache key SHALL include the org. A credential cached for org A and server slug S SHALL never be used for org B and slug S (L17b-4): a test with two orgs, the same slug, and different credentials SHALL assert each broker body carries only its own org's credential and that an org with no credential never receives another org's.
5. THE credential SHALL be removed from memory-bearing structures as soon as the request ends where the language allows (no long-lived reference, no module-level cache, no `lru_cache`), and no credential SHALL be held in a closure that outlives the request other than the bounded cache.
6. THE structural gates SHALL include a test that fails when any value type or exception that can hold Credential_Material has a `__repr__`/`__str__` that prints it, and `lint/check_secret_headers.py` or an equivalent AST check SHALL fail on a log call, audit call or exception that takes a credential-typed value as an argument.
7. THE broker client's own failure text and the registry's failure text SHALL NOT include the request headers.
8. THE credential-bearing broker body SHALL not be written to any retry buffer, dead-letter store, request-id fingerprint (`_request_id` hashes the raw message, which does not include the header; this SHALL stay true and be tested) or debug dump.
9. WHEN `auth_type` changes from authenticated to `none`, or the credential is deleted, THE gateway SHALL stop sending a credential within the registry TTL, and SHALL not fall back to a previous value.

### Requirement 15: Per-key tool controls on Principal

**User Story:** As an operator, I want to restrict an API key to named tools and cap its calls, as v1 did.

#### Acceptance Criteria

1. THE `Principal` value type SHALL gain two fields: `mcp_allowed_tools` and `mcp_max_tool_calls`. `mcp_allowed_tools` is a frozen, order-insensitive, deduplicated set of tool names where the empty set means unrestricted (v1 semantics). `mcp_max_tool_calls` is a non-negative integer where 0 means unlimited. Both SHALL have defaults that preserve today's behaviour so every existing construction site and fixture keeps working. `Principal` SHALL stay frozen and slotted.
2. THE control-side state projection SHALL read both fields from `core_gatewayapikey` (`SourceKey` and the source query gain them; the query lists columns explicitly, no `SELECT *`), and SHALL write them into the signed KEY record body.
3. `principal_of` SHALL parse them with strict validation: `mcp_allowed_tools` must be a list of non-empty strings within a bound on count and name length; `mcp_max_tool_calls` must be a non-negative integer (not a bool, not a float with a fraction) within a bound. A record whose MCP fields are present and malformed SHALL raise the same `StoreDataUnavailable` as other malformed key fields (fail closed: the key does not resolve), and SHALL NOT be treated as unrestricted.
4. A record that lacks both fields SHALL resolve as unrestricted and unlimited (records signed before this card remain valid, and a rolling deploy is safe in both directions: an old gateway that ignores unknown fields SHALL keep working with new records; verify that `principal_of` and the signature check ignore unknown fields or that the canonical body change does not invalidate the signature).
5. THE record body SHALL remain canonical and deterministic: the same inputs SHALL produce the same bytes (list sorted). A change to only these fields SHALL produce a changed record and a feed delta, so the identity cache invalidates it.
6. THE bounds (max tools per key, max tool-name length, max cap value) SHALL come from the ResourceContract (`runtime/resources_mcp.py`), with a refusal at projection time when control holds a value over the bound (the key is projected as it is today and an operator-visible note is recorded; it is NOT projected as unrestricted). Declared choice: an over-bound allow-list SHALL be projected as a deny-all-tools allow-list (a reserved sentinel that matches no tool name), so MCP tool calls for that key are refused and the key's other surfaces are unaffected. It SHALL never be projected as unrestricted.
7. THE console/control UI is out of scope, but the control serializer for the key SHALL already expose both fields (they exist), and a contract test SHALL pin the field names between control, projector and `principal_of`.

### Requirement 16: Allow-list enforcement before the broker

**User Story:** As an operator, I want a restricted key to be unable to call, or even see, tools it is not allowed.

#### Acceptance Criteria

1. WHEN the method is `tools/call` AND the key's `mcp_allowed_tools` is non-empty AND the tool name is not in it THEN THE MCP_Surface SHALL return `mcp_tool_not_permitted` (HTTP 403, JSON-RPC error with `error.data.code`) and SHALL make zero broker calls and zero credential calls (L17c-1).
2. THE check SHALL run after identity and plan resolution and before the registry gate's credential fetch, the destination gate and the broker call. Any budget charge already taken SHALL be refunded, because no broker call was made. It SHALL use the exact tool name from `params.name` (string equality; no case folding, no prefix match, no wildcard).
3. IF `params.name` is missing or not a string on `tools/call` THEN the request SHALL be refused `mcp_invalid_params` as today, before the policy lookup.
4. WHEN the method is `tools/list` AND the allow-list is non-empty THEN THE MCP_Surface SHALL return only tools whose name is in the allow-list, in addition to the existing registry filter. The filter SHALL run before the reply is scanned and returned. IF no listed tool is allowed THEN it SHALL return an empty list (a valid result), not an error.
5. THE allow-list SHALL apply to `tools/call` and `tools/list` over every Client_Transport (HTTP POST, SSE-framed POST, WebSocket frame) with one shared function, so there is no transport on which it can be bypassed.
6. THE allow-list SHALL apply per request, using the Principal resolved on that request. A cached session or WebSocket connection SHALL NOT keep an old allow-list (Requirement 18).
7. A tool-denied call SHALL be audited with the tool name, key id and the deciding code, and SHALL NOT be counted against the call cap (the cap counts calls that reach the broker).
8. FOR an unrestricted key (`mcp_allowed_tools` empty and `mcp_max_tool_calls` 0) THE behaviour SHALL be identical to GW17: byte-identical responses on a fixture set, no extra store read, no extra latency path (L17c-4).
9. THE other `methods` (`resources/read`, `prompts/get`, `completion/complete`) are NOT tool calls and SHALL NOT be restricted by the allow-list in this card. This is a recorded decision (v1 restricts only tools).

### Requirement 17: Call cap, counted off the request path

**User Story:** As an operator, I want a key's tool calls capped per window, with a clear 429, and without a store round trip on every call.

#### Acceptance Criteria

1. WHEN the key's `mcp_max_tool_calls` is greater than 0 THEN THE MCP_Surface SHALL count each `tools/call` that reaches the broker against that key in a window of the contract-derived length (D-6).
2. WHEN the count in the window has reached the cap THEN a further `tools/call` SHALL be refused before the broker with `mcp_tool_call_cap_exceeded` (HTTP 429, JSON-RPC error with `error.data.code`), with a `Retry-After` header in whole seconds, at least the declared minimum (`MIN_RETRY_AFTER_S`) and no more than the window, and `should_retry` semantics consistent with the existing admission 503/429 shape (L17c-2). The count SHALL be per key, so other keys of the same org SHALL be unaffected, and other orgs' keys SHALL be unaffected.
3. THE counter SHALL be maintained under the GW06 lease idiom: a store-backed allowance leased in chunks and spent locally in RAM on the request path with a synchronous check and no store call, and refilled asynchronously off the request path, with generation tagging so a changed cap invalidates held leases. The request path SHALL make zero store calls for the cap check (a test with a store that raises on any call during the request shall pass, as the lease tests do).
4. THE cap SHALL NOT be multiplied by workers or replicas: the total across N workers SHALL stay at or below `cap` plus the declared lease overshoot bound, published as a metric (as the budget lease does). The overshoot bound SHALL be a contract-derived value and the test SHALL assert it with N workers sharing one store.
5. WHEN the store is unreachable THEN the worker SHALL spend only its held remaining allowance, and when it is exhausted SHALL refuse with the cap code (fail closed), NOT admit freely. It SHALL NOT return `shared_state_unavailable` for a cap problem (same narrowness rule as `budget_unavailable`). This is the deliberate difference from v1, which failed open on a Redis error; it SHALL be recorded.
6. A refusal at the cap SHALL refund any budget charge already taken for the request (the request did not reach the broker), release the admission slot once, and write an audit record.
7. THE count SHALL be incremented only when the call is about to be forwarded (after every other refusal), so a request refused for any other reason (block, tool disabled, destination refused, needs_reauth) SHALL NOT consume the cap. A broker failure after the increment SHALL still count (the call may have been sent), the same as the "no post-send retry" rule.
8. THE counter keys SHALL be hash-tagged `{rv2}` store keys defined in `runtime/store_keys.py`, include the key id, carry a TTL, and expose no raw key material.
9. THE cap SHALL apply to every Client_Transport with one shared code path.
10. THE lease, refill, and window numbers (chunk, watermark, window seconds, TTL, overshoot bound, retry-after jitter) SHALL come from the ResourceContract via `runtime/resources_mcp.py`. There SHALL be no capacity literal outside the allowed modules.
11. THE metrics SHALL be label-free, seeded at zero, and published through the existing observability producer pattern: tool-call cap refusals, allow-list refusals, async refills, lease overshoot, and `fail_open_total` pinned 0.

### Requirement 18: Changes take effect within the freshness bound

**User Story:** As an operator, I want an edit to a key's tool controls to take effect quickly and predictably.

#### Acceptance Criteria

1. WHEN control changes a key's `mcp_allowed_tools` or `mcp_max_tool_calls` THEN the next signed KEY record and feed delta SHALL carry it, and the gateway SHALL enforce the new value within the Identity_Freshness_Bound (the feed lag bound of the identity cache), with no restart (L17c-3).
2. WHILE a cached Principal is older than the bound without a fresh feed position THEN the existing identity fail-closed behaviour applies (`shared_state_unavailable`), and no MCP-specific path SHALL serve an older Principal than chat would.
3. A changed cap SHALL change the lease generation so that previously leased allowance is dropped, not spent. A lowered cap SHALL not be exceeded by more than the declared overshoot bound after the freshness bound.
4. A WebSocket connection or session open when the policy changes SHALL pick up the new policy on its next message, because it re-resolves the Principal per message (Requirement 5.5 / 16.6).
5. A test with an injected clock SHALL show: change applied at t0, a call at t0 + bound sees the new policy, and the test SHALL name the bound it uses and read it from the same constant the identity cache uses.

### Requirement 19: Declared error vocabulary

**User Story:** As an MCP client developer, I want stable codes for every new refusal.

#### Acceptance Criteria

1. THE new value-codes SHALL be added to the one Error_Envelope table and the one JSON-RPC mapping, each with a status and an error type: `mcp_tool_not_permitted` (403, permission_error), `mcp_tool_call_cap_exceeded` (429, rate_limit_error), `mcp_session_invalid` (404, not_found_error), `mcp_origin_refused` (403, permission_error), `mcp_org_mismatch` (403, permission_error), `mcp_upstream_needs_reauth` (401, authentication_error where that type exists, else 403 permission_error), `mcp_upstream_credential_unavailable` (503, service_unavailable), `mcp_upstream_credential_invalid` (502 or 403, declared), `mcp_upstream_insecure_transport` (403, permission_error), `mcp_transport_unsupported` (400, invalid_request_error), `mcp_session_limit` (429, rate_limit_error), plus the OAuth RFC 6749 error set on the OAuth routes only.
2. EACH code SHALL have a fixed message with no matched text, no upstream text, no secret, no exception text and no stack trace.
3. A drift test SHALL fail if a code used in `edge/mcp*.py`, the OAuth modules, or the credential modules is missing from the table, or the table has a code with no producer.
4. THE 429 codes SHALL carry `Retry-After`. A 429 or 503 with `Retry-After` SHALL keep `x-should-retry: false` for the cap code, because retrying inside the window cannot succeed, consistent with the admission shape.

### Requirement 20: Audit and console for the new paths

**User Story:** As a console operator, I want the new refusals and OAuth events to show up in the audit stream like other decisions.

#### Acceptance Criteria

1. EVERY refusal introduced here SHALL emit one audit record with route `mcp` (or `oauth` for OAuth routes), the declared code, the key id (not the key), the org id, the server slug and tool name where there is one.
2. THE Console_Object rule from GW17 stays: a refusal before the input scan has no Console_Object. The tool-policy and cap refusals happen before the scan and SHALL carry none.
3. NO audit record, Console_Object, trace or log line SHALL contain Credential_Material, a MCP_Access_Token, an OAuth code, a PKCE verifier, an API key, a session id in clear, or `Finding.evidence`.
4. THE audit schema validation (GW14b console schema) SHALL still pass.

### Requirement 21: Structural constraints

**User Story:** As a maintainer, I want this work to keep the layered architecture and gates, so it can ship with the rest of v2.

#### Acceptance Criteria

1. THE modules SHALL respect the import-linter layer contract (edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain), the "Resolve imports only domain" contract, and the "No HTTP framework outside edge" contract. Both import-linter contracts SHALL be reported as kept. The expected placement is: value types in `domain` (Principal fields, a credential type with no I/O); lease and counter in `admit` (as `lease.py`, which imports runtime and domain only); the credential client, OAuth state store protocol and broker body assembly in `dispatch` or `edge` as the layer rules allow; all HTTP request, response, SSE and WebSocket objects in `edge` only.
2. THE `admit` layer SHALL NOT import `edge` and SHALL return frozen verdict values that `edge` renders, as for GW19 and GW06.
3. NO capacity literal SHALL appear outside the allowed modules. THE capacity-literal gate (`lint/check_capacity_literals.py`) SHALL be extended so its allowlist is a frozenset of exactly `runtime/resources.py` and `runtime/resources_mcp.py` (D-5), with `tests/gates/` positive and negative cases (a literal in `runtime/resources_mcp.py` passes; the same literal in `runtime/other.py`, in `admit/mcp_*.py` and in an unlisted `resources_x.py` fails). `runtime/resources.py` SHALL NOT be edited, because it is at the 800-line cap.
4. NO module-level mutable state: no module-level dict, list, set, or cache that is mutated; no `functools.lru_cache`; no global counters. `lint/check_no_module_mutable.py` SHALL exit 0. All stores, caches and counters SHALL be instances owned by the app and injected.
5. EVERY new value type (verdicts, credentials, sessions, tokens, policy views, bounds) SHALL be a `@dataclass(frozen=True, slots=True)` or an equivalent immutable type. `lint/check_frozen_dataclasses.py` SHALL exit 0.
6. NO module SHALL exceed 800 lines and NO function SHALL exceed 120 lines (`lint/check_sizes.py`). `edge/routes.py` is at 799 lines, so route registration SHALL go through a new surface-entries module (the shipped `edge/surface_entries.py` pattern) and `routes.py` SHALL NOT grow past the cap. `edge/mcp.py` is 421 lines, so each new concern (sessions, SSE framing, WebSocket, OAuth, credentials, policy) SHALL be its own module with one responsibility.
7. HTTP objects SHALL appear only in `edge`. `lint/check_http_outside_edge_resolve.py` SHALL exit 0.
8. THE `Principal` change SHALL be additive and keyword-safe: other cards add fields to the same type (GW31 adds a project), so each new field SHALL have a default and be placed so a merge of two cards is a union, not a rewrite. The same holds for `lint/check_capacity_literals.py` and `edge/errors.py` (GW27 and GW34 also edit them): additions SHALL be append-only blocks.
9. NO fail-open: a check that cannot be computed (tool policy parse, counter, credential fetch, token lookup, session lookup, origin parse) SHALL refuse. `FAIL_OPEN` SHALL stay 0, and a test SHALL assert it after each failure injection.
10. NO secret SHALL be committed. Test fixtures SHALL use obviously fake values with a fixed prefix the leak harness knows.
11. NO new base runtime dependency SHALL be added to `gateway_v2`. Optional libraries (`websockets`, `httpx`) SHALL be test-only or an extra, imported lazily, and a test depending on one SHALL `importorskip` it.
12. THE state_control tree (outside `root_packages`) SHALL stay `mypy --strict` clean, and the projector change SHALL keep its determinism tests green.

### Requirement 22: Offline gates and tests

**User Story:** As a maintainer, I want the card reviewable on a laptop, with every offline gate green and every deferred item named.

#### Acceptance Criteria

1. THE full offline pytest suite (run the way the container-run lane runs it, no live services) SHALL pass with zero failures. Tests that the new behaviour supersedes (for example any test that asserts `initialize` answers without a key, or that a WebSocket scope is declined for an MCP path, or that `mcp_upstream_auth_unsupported` is returned for a bearer server) SHALL be updated deliberately, listed in the change summary, and SHALL NOT be deleted to get green.
2. `mypy --strict` SHALL pass over `gateway_v2`, `state_control` and `audit_control`. `ruff` SHALL pass. `import-linter` SHALL report all contracts kept. The five AST gates (capacity literals, no module mutable, frozen dataclasses, http outside edge, tenant scale) and `check_sizes` SHALL exit 0, except for the documented pre-existing finding at `state_control/valkey.py:100` (`check_tenant_scale`), which this card SHALL NOT touch or hide.
3. PROPERTY tests SHALL run at 100 iterations or more, seeded `random.Random` (house idiom, no new library), and each SHALL carry an in-source tag of the form `# Feature: gw32-mcp-transport, Property N: <title>` (mirroring the shipped specs). Required properties, at least:
   - P1 Identity first: for any method and any malformed or absent credential, zero registry, broker and credential calls and no state created.
   - P2 Allow-list: for any allow-list and tool name, a call is forwarded iff the name is in a non-empty list or the list is empty; `tools/list` output equals the registry-filtered list intersected with the allow-list; never reorders or mutates kept tools.
   - P3 Cap conservation: for any cap, worker count and call schedule, total admitted calls in a window is at most cap plus the overshoot bound; other keys unaffected.
   - P4 Cap fail-closed: for any store failure schedule, no call is admitted beyond the held allowance.
   - P5 Credential secrecy: for any credential and any scenario in the harness (success, upstream error, refresh failure, reflecting upstream, timeout), the Credential_Material appears in no captured sink.
   - P6 Org isolation: for any two orgs, slugs and credentials, a broker body never carries the other org's credential.
   - P7 Header validation: for any header name and value, a credential is rendered iff the name and value pass the declared rules; no CR/LF/NUL reaches the broker body.
   - P8 Refresh single flight: for any number of concurrent requests with one expired token, at most one refresh call; a failed refresh never yields a forwarded request.
   - P9 PKCE and redirect: for any code, verifier and redirect URI, a token is issued iff client, exact redirect and S256 verifier all match and the code is unused; a code is never redeemable twice.
   - P10 Session binding: for any session, key, org and slug triple, a session is accepted iff all three match and it is live.
   - P11 Principal round trip: for any allow-list and cap, projector output parsed by `principal_of` equals the input (set semantics), and any malformed MCP field fails closed.
   - P12 SSE framing: for any JSON-RPC body, the SSE framing decodes (shipped `SSEDecoder`) to the same body, and a body containing newlines cannot inject an event.
4. UNIT and integration tests SHALL cover, at least: every acceptance criterion marked as a refusal (zero broker calls asserted with a counting fake broker); the four L17b tests in their offline form (L17b-1 through L17b-4, Requirement 14); the four L17c tests in their offline form (L17c-1 through L17c-4); route-table pinning; WebSocket frames, close codes, re-check and bounds against the ASGI harness; OAuth flows end to end in process (register, authorize, token, MCP call with token, replay, wrong verifier, wrong redirect, revoked key); `oauth/start`, callback and status; and the rolling-deploy compatibility case in Requirement 15.4.
5. EACH gate failure mode SHALL have a negative control: a test or recorded run that shows the gate or test turns red when the guarded behaviour is removed (for example, moving `_local` before identity, removing the allow-list check, or adding a log line with a credential).
6. THE existing chat, completions, responses, embeddings, GW17 MCP tests SHALL keep passing, except those listed under 22.1.
7. THE card SHALL NOT be marked done in the runbook, and the progress file SHALL record GW32, GW17b and GW17c as "code on this tree, offline gates green, live gate not run" with the DEFERRED list above. The unattended run SHALL NOT start the Docker or staging stack and SHALL NOT claim any live line.
8. THE implementation SHALL end with a short human handoff file naming each DL item, the exact command or check to run, and each open decision (D-1 to D-7), so the human can run the live gate.

## Recorded gaps and risks (not fixed by this spec)

- **Surface filtering.** `Rule.surfaces` stays ignored, per the operator decision recorded under GW17.
- **Sandbox holes** inherited from GW17 stay open (DNS rebinding only partly closed, `MCP_AGENT_ALLOW_INTERNAL_HOSTS`, outbound NAT per org network, the broker key as one shared secret compared in non-constant time, runc fallback). The credential path therefore does not make the sandbox a trusted boundary for the credential, and the GW17b security design says so.
- **The credential reaches the broker process.** The broker and sandbox agent see the header in memory. Hardening the broker is outside this spec.
- **SSE progress interleave** stays untestable through the broker's one-envelope reply.
- **The access-token model differs from v1** (D-3). Clients that stored a v1-returned API key as the token keep working, because the identity step still accepts a key as a Bearer on MCP routes.
- **Cross-card edits.** GW27, GW31 and GW34 edit `check_capacity_literals.py`, `Principal`, and `edge/errors.py`. Merge conflicts are possible and are resolved as unions (Requirement 21.8).

## Out of scope

- The live Docker and staging gate, every DL item above, and any claim of a live result.
- Console UI changes (key edit form, server credential form, reauth banner). The control API fields exist or are added; the pages are GW35.
- Changes to v1 `gateway/ai_mesh_gateway/` code, to the broker (`services/mcp-broker/`) or to the sandbox agent. Where the broker lacks a behaviour, it is recorded as a gap.
- v1's bare REST routes, ext-proxy routes and internal discover/tools-call routes (their ledger rows stay `retire`).
- RAG, vector, chat, embeddings and responses behaviour, other than the identity-resolver change in Requirement 7 which must not change them.
- Per-tool or per-server cap, a cap on non-tool methods, a per-org cap, and allow-lists for `resources/read`, `prompts/get` and `completion/complete`.
- Enforcing `Rule.surfaces`.
- A redirect plan for the v1 URL (D-1 chose to serve it).
- Client migration notices.
- Real-cloud capacity runs.

## Requirement trace

| Source item | Requirement |
|---|---|
| V-17 / progress: `initialize`/`ping` answer before identity | 1 |
| GW32: serve v1 endpoint `/gateway/{org}/mcp/{srv}` (streamable HTTP, sessions, GET) | 2, 3, 4 |
| GW32: gateway as OAuth 2.1 server (`/.well-known/oauth-*`, `/oauth/register`, `/oauth/authorize`, `/oauth/token`) | 6, 7 |
| GW32: MCP OAuth setup routes (oauth/start, callback, status) | 9 |
| GW32: HTTP+SSE and WebSocket against real servers | 5, 8 |
| Owner decision 11 (MCP client URL) | D-1, 2 |
| GW14b: `oauth/start` ledger row needs a build owner (GW32, GW35) | 9.6 |
| GW17b: credential path; list, logs and audit stay clean | 10, 11, 12, 14 |
| GW17b: refresh and `needs_reauth` | 13 |
| GW17b L17b-1 | 14.1, 14.2 |
| GW17b L17b-2 | 13.1, 13.4 |
| GW17b L17b-3 | 14.3 |
| GW17b L17b-4 | 14.4 |
| GW17c: two fields on Principal | 15 |
| GW17c: 403 and filtered `tools/list` | 16 |
| GW17c: 429 with Retry-After, off request path, GW06 lease idiom | 17 |
| GW17c L17c-1 | 16.1, 16.4 |
| GW17c L17c-2 | 17.2 |
| GW17c L17c-3 | 18 |
| GW17c L17c-4 | 16.8 |
| Constraints: layering, no capacity literal, no module-level mutable, frozen/slotted, fail-closed, HTTP in edge | 21 |
| Constraints: offline gates, property tests at >= 100 iterations tagged in source | 22 |
| Deferred live gate | DL-1 to DL-7, 22.7, 22.8 |


## Amendments from design review (iteration 2)

- **A-1 (R4.3, P12):** `SSEEncoder`/`SSEDecoder` are chat-delta shaped and cannot carry JSON-RPC. MCP SSE uses `edge/wire/mcp_sse.py` with the same framing rules (ASCII-escaped compact JSON, `data:` grammar); P12 decodes with a helper using the `SSEDecoder` data-field grammar, cross-checked by a test.
- **A-2 (R5.7):** a WebSocket holds a per-connection idle weight for its life and a full admission slot per message (owner decision D-8).
- **A-3 (R19.1):** `mcp_upstream_needs_reauth` is 403 `permission_error`, never 401.
- **A-4 (R17.8):** cap store keys live in `runtime/store_keys_mcp.py` with the same namespace and `{rv2}` tag.
