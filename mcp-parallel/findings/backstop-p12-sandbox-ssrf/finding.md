# BACKSTOP hardening — sandbox agent upstream SSRF / DNS-rebinding guard (CHG-0067)

- **Item:** G3 item 12 (egress-lockdown / SSRF). Sandbox-side analogue of CHG-0065 (gateway ext-proxy SSRF).
  Closes the follow-up documented in CHG-0066.
- **Change-id:** CHG-0067 (2026-07-02)
- **Type:** SSRF (missing resolved-IP guard on the sandbox's untrusted-upstream dialer), fail-closed.

## Gap
The per-tenant sandbox agent (`services/mcp-broker/sandbox-image/agent/upstream_manager.py`) dials the
registered upstream MCP server. `_validate_upstream` only matches the host STRING against
`allowed_hosts` — it NEVER resolves the hostname or checks the resolved IP. So an allowlisted host that
RESOLVES to an internal address (DNS rebinding / hijack, or a tenant registering a server whose hostname
points internally) is dialed from inside the sandbox. The per-org sandbox network is `internal=false`
(open NAT — the isolation-posture residual), so the sandbox CAN reach:
  * the cloud-metadata endpoint `169.254.169.254` (→ IAM credential theft),
  * loopback / link-local, RFC-1918 internal services.
CRITICALLY, this is the PRIMARY sandbox-routed path: the gateway's `_adapter_forward` → `broker_send_rpc`
does NOT call `is_safe_outbound_url` for the sandbox-routed upstream (only ext-proxy [CHG-0065] and the
internal paths do), so the sandbox agent's `_validate_upstream` was the ONLY runtime check — and it was
allowlist-string-only. A tenant registering an MCP server with an internal-resolving hostname → SSRF from
the shared infra's sandbox.

## Fix — `upstream_manager.py`
- `_resolved_ip_blocked(ip_str)`: returns a reason if an IP is a cloud-metadata endpoint
  (169.254.169.254 / fd00:ec2::254) or `is_private`/`is_loopback`/`is_link_local`/`is_reserved`/
  `is_multicast`/`is_unspecified`.
- `async _assert_upstream_not_ssrf(host)`: for a literal-IP host checks it directly; for a hostname
  resolves via the event loop's ASYNC `getaddrinfo` (non-blocking; OS-cached) and rejects if ANY
  resolved IP is blocked. FAIL-CLOSED on a resolution failure. `MCP_AGENT_ALLOW_INTERNAL_HOSTS` bypasses
  (dev / self-hosted internal upstreams).
- Called in `_get_session` right after `_validate_upstream` — BEFORE any connection is opened, so the
  block happens before the sandbox dials the upstream.

## Verification
- `cd services/mcp-broker && ./.venv/bin/python -m pytest sandbox-image/agent/tests/test_upstream_proxy.py -q -k "not websocket"`
  → 13 passed. New: (1) an allowlisted host `localhost` that resolves to 127.0.0.1 → `-32002 egress
  denied: host 'localhost' -> internal/reserved address (127.0.0.1)` (BEFORE any connection); (2) a
  host resolving to a PUBLIC IP (getaddrinfo mocked) → NOT blocked, proceeds to the tools/list result.
  The 3 `websocket` tests HANG in this env PRE-EXISTINGLY (unrelated — real ws / async-loop issue; this
  change is in the upstream-session setup, exercised by the streamable-http tests which all pass).
- Existing tests stay hermetic: the app-load fixture sets `MCP_AGENT_ALLOW_INTERNAL_HOSTS=1` (they use
  `mcp.example.com`, NXDOMAIN in this env, and mock the socket); the SSRF tests un-set it.

## Residual / follow-up
The proper defense remains network-level egress lockdown (per-org sandbox `internal=true` + broker-
proxied allowlist, or iptables/eBPF) — an INFRA task (item 12, needs the deploy host). This code-level
resolved-IP guard is defense-in-depth that works TODAY regardless of the network posture. A gateway-side
`is_safe_outbound_url` on the sandbox-routed upstream (parity with ext-proxy) would add a second layer.
