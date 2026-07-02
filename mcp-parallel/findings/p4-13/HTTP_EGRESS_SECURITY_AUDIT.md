# P4.13 — HTTP-via-sandbox egress/OAuth security audit (adversarial, PASS)

With streamable-http now routed through the per-org sandbox, audited the new attack
surface. All defenses hold:

| vector | defense | live check |
|--------|---------|-----------|
| upstream host ∉ allowlist | agent `_validate_upstream` → `-32002 egress denied` | ✅ `egress denied: host '…' not in allowlist` |
| redirect to non-allowlisted host | httpx `follow_redirects=False` (`_get_session`) | ✅ code — redirect returned, never followed |
| unicode/punycode host spoof | `_normalize_host` via `idna.encode` before allowlist compare | ✅ code |
| IP-literal host | separate `ipaddress` allowlist branch | ✅ code |
| internal/metadata target at registration | gateway SSRF guard `is_safe_outbound_url` rejects | ✅ (B1/#14 SSRF guard) |
| sandbox as OAuth client | `oauth_client_role=client` → `-32602 forbidden in sandbox` | ✅ `oauth_client_role=client is forbidden in sandbox` |
| cross-org OAuth Bearer leak | token store keyed by `(org_slug, server_url)` (`mcp_oauth_proxy._token_redis_key`); gateway injects per-org token via `get_stored_token(org_slug, url)` | ✅ code — org-scoped |
| gateway dials upstream directly | all transports via sandbox; gateway→upstream sockets = 0 | ✅ EGRESS_HTTP_PROVEN.md |

## Conclusion
The HTTP-via-sandbox path preserves the isolation model: per-org sandbox is the sole
dialer, egress is allowlisted (no redirect/unicode/IP bypass), OAuth is injected
per-org (control-plane owned; sandbox never runs the OAuth client). No new
cross-tenant or SSRF surface introduced by §3.
