# GW17b security design (copied from the approved GW32 design, section "GW17b security design")

Source: `.kiro/specs/gw32-mcp-transport/design.md`.

## GW17b security design

This section is the deliverable of R10.

### Where each credential lives, who reads it, how it moves, how long it is held

| Credential | At rest | Who can read it | Transport | In gateway memory | Dropped when |
|---|---|---|---|---|---|
| Bearer token | `MCPServerRegistration.auth_token`, Fernet (`EncryptedCharField`) in Postgres | Control process (decrypts on read); console users cannot (write-only serializer fields) | Control to gateway: `GET internal/credential/`, internal network, internal key, `Cache-Control: no-store`. Gateway to broker: `upstream.headers` on `POST /v1/sandbox/{slug}/rpc` | `CredentialCache` entry, per worker RAM, ≤ `registry_ttl_s` | TTL; `mcp:scan_ver` change; `needs_reauth`; `auth_type` change |
| Basic user/password | `auth_username`, `auth_password`, Fernet | as above | as above | as above (base64 form derived only inside `render`) | as above |
| Custom header | `auth_header_key` (plain, not secret), `auth_header_value` Fernet | as above | as above | as above | as above |
| OAuth access token | `auth_token`, Fernet; expiry in `oauth_token_expires_at` | Control | as above | as above | as above, plus on refresh success (replaced) |
| OAuth refresh token and client secret | `oauth_refresh_token`, `oauth_client_secret`, Fernet | Control only | **Only** on `internal/credential/?need_refresh=1`, only when the gateway holds an expired/expiring access token and the refresh lock | Local variables of the one refresh call; **never cached**; no closure outlives the call | Function exit (references released; CPython cannot zero memory, stated as a residual risk) |
| PKCE verifier (upstream setup) | `oauth_code_verifier`, Fernet, TTL via `oauth_state` | Control | Gateway to control `internal/oauth-flow/`; consumed once | Local in `start` and `callback` | Function exit |
| `MCP_Access_Token` (client side) | Valkey as HMAC only | Nobody can read the token back | Authorization header on the client connection | RAM table keyed by HMAC | `token_cache_ttl_s` |

### Threats and controls

| Threat | Control | Test that shows it |
|---|---|---|
| Secret in list responses | List serializer unchanged; credentials only on `internal/credential/`; regression walks list JSON | `control` test `test_list_has_no_credentials` |
| Secret in gateway/control/broker/sandbox logs | Credential types redact in `repr`/`str`; no log call receives them (AST gate); registry/broker/credential failure text is fixed value-codes; control views never log request bodies for these endpoints | L17b-1 harness P5; `check_credential_flow` |
| Secret in audit, console object, trace | `RenderedHeaders` and `UpstreamCredential` are never passed to `emit_chat_audit`, `mcp_console`, trace builders; audit `detail` is built from a closed key set | P5 over audit and console sinks |
| Secret in exceptions / error envelopes | `__reduce__` refuses; `CredentialRefusal` holds a code only; `except Exception` blocks do not format `exc` | P5 injected-failure variants |
| Secret in metrics labels | Label-free producers (house rule); credential never reaches `observe_*` | exposition captured in P5 |
| Secret scanned/stored/masked as input text | Credential is never in `fold`/`Finding`; `Finding.evidence` is never serialised (R2-05) | structural test: fold leaves compared to credential forms |
| Cross-org credential use | Cache key `(org, slug)` with `org` from the key's org map only; control endpoint scopes by `X-Org-Slug` | P6, L17b-4 |
| Confused deputy via slug-only cache | Same; the registry cache is already `(org, slug)` | L17b-4 |
| DNS rebinding on gateway-side OAuth calls | `resolve_checked` + `PinnedTransport` dial only the approved IP (zero second resolution) | rebinding resolver test, zero bytes |
| SSRF to attacker-chosen URL that receives the credential | URL comes from control's registry, not the client; `check_remote` (resolve-then-check, loopback/metadata refused) before attach; `allowed_hosts == [host]` | destination-gate tests with credential |
| Redirect forwards credential to another host | Credential goes only to the broker; refresh and discovery calls follow no redirect without re-running `check_remote` and send the credential to the token endpoint only | refresh test with 302 |
| Downgrade to http | `mcp_upstream_insecure_transport` unless host explicitly allowed | R12.5 tests |
| Revoked credential still cached | TTL ≤ `registry_ttl_s`; poll drop; `needs_reauth` drop; `auth_type` check | L17b-3 injected clock |
| Refresh-token theft and rotation | Refresh token only on the refresh path, never cached; rotation write-back; failed persistence of a rotated token forces `needs_reauth`; family revocation for client-side tokens | P8, refresh tests |
| Replay of a refresh | Single use rotation; compare-and-set on `credential_version` | write-back idempotence test |
| Credential in a core dump or `repr` | `repr` redacted, pickling refused; core dumps are residual | repr/pickle test |
| Header injection via name/value | RFC 7230 token check, CR/LF/NUL/non-printable refusal, deny list | P7 |
| Upstream reflects the secret to the client | Exact-match reflection guard, withhold | P5 reflecting-upstream scenario |
| Timing/enumeration on the internal endpoint | `compare_digest` on the key; 404 body identical for unowned and absent; throttle | control tests |

### Residual risks (stated, not claimed away)
- Query strings of `GET /gateway/oauth/callback` (`code`, `state`) and `GET /oauth/authorize` reach ASGI server access logs. Both are single-use and PKCE-bound, so the risk is low; deploy note: disable query-string access logging for these paths. The P5 secret-in-logs claim covers gateway sinks only.
- An 8-character sliding-prefix match can withhold a legitimate reply when a secret shares a standard prefix (`ghp_`, `sk-proj-`) with text a tool returns. Mitigation: `WELL_KNOWN_PREFIXES` (a frozen tuple in `domain/mcp_credential.py`: `ghp_`, `gho_`, `github_pat_`, `sk-proj-`, `sk-`, `xox`, `Bearer `, `AKIA`) are stripped from the secret before the sliding-prefix rule, so the run must lie beyond the prefix and still be 8 characters; the full-value and encoded-form rules are unaffected. A secret that is entirely a known prefix plus under 8 characters is refused by the validator. Availability cost recorded: a reply echoing 8 characters of the non-prefix part is withheld.
- Credential cap semantics: the tool-call cap counts attempts that reached the broker, not successes (see "Cap").

- The broker and sandbox agent hold the header in process memory and may log it; the broker is out of scope. The credential path does **not** make the sandbox a trusted boundary. GW17's open sandbox holes (DNS rebinding only partly closed, `MCP_AGENT_ALLOW_INTERNAL_HOSTS`, shared broker key compared in non-constant time, runc fallback) stay open.
- CPython cannot reliably zero a `str`; "dropped from memory" means "no live reference".
- The control-to-gateway link is as safe as the internal network; the https requirement is enforced in the gateway client but a loopback or declared-plaintext host weakens it by operator choice.
- A compromised gateway worker can read the cached credentials of the servers it has served in the last TTL.
- Control stores Fernet-encrypted secrets with a key from `FIELD_ENCRYPTION_KEY` with a `SECRET_KEY` fallback (existing behaviour, unchanged here).
