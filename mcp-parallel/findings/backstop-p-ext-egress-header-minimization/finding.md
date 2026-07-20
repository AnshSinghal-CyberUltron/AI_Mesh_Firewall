# CHG-0110 — ext-proxy egress leaked client IP / internal topology / org slug to third-party servers

**Change-id:** CHG-0110
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (privacy / infra-topology / tenant-identity leak to an untrusted third-party external MCP server — NOT a credential leak; credentials were already stripped by CHG-0033).
**Area:** HARDEN 1.4 — context minimization / least-privilege (egress header minimization).
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (`_EXT_ROUTING_HEADERS` + `_ext_proxy_forward_headers`); `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+1 test).
**Whose work it touches:** the owning-session proxy `_ext_proxy_forward_headers` (extends the CHG-0033 least-privilege header set).

## Root cause

`_ext_proxy_forward_headers` builds the outbound header set for the transparent EXTERNAL proxy
(`ext_mcp_proxy` → allowlisted third-party MCP servers). It is a **denylist**: it drops credentials
(`authorization`/`proxy-authorization`/`cookie`/`set-cookie`/`x-api-key`, CHG-0033), `x-gateway-*`, and
hop-by-hop headers — but **forwards everything else**. A denylist forwards-by-default, so request-ROUTING /
client-IDENTITY / topology headers leaked to the untrusted third party:

- `x-forwarded-for: 203.0.113.9, 10.0.0.2` — the caller's real **public IP** + an **internal IP**.
- `x-real-ip: 203.0.113.9` — the caller's real IP.
- `x-forwarded-host: gw.internal.local`, `via: 1.1 gw-internal` — the **internal gateway host + proxy chain**.
- `referer: https://gw.internal/org/demo/chat` — the **internal URL AND the org/tenant slug** (`org/demo`).

So a third-party (or compromised allowlisted) external MCP server learned who the caller is (IP), the internal
network topology, and which tenant/org was calling it.

### Byte-level truth (pre-fix) — driving the REAL `_ext_proxy_forward_headers`

All six headers egressed; the forwarded set disclosed `203.0.113.9`, `10.0.0.2`, `gw.internal`, and `org/demo`.
(Credentials `authorization`/`cookie`/`x-api-key`/`x-gateway-internal-key` were correctly stripped — CHG-0033.)

## The fix (CHG-0110)

`_ext_proxy_forward_headers` now also drops:
- `_EXT_ROUTING_HEADERS = {x-real-ip, forwarded, via, referer, referrer}`, and
- any `x-forwarded-*` header (prefix match).

A third-party external MCP server now sees ONLY protocol/benign headers (`content-type`, `accept`,
`mcp-session-id`, `mcp-protocol-version`, `user-agent`) plus the gateway's OWN injected upstream OAuth bearer —
NEITHER the caller's IP, NOR the internal topology, NOR the tenant slug. Credential-stripping (CHG-0033) is
unchanged, and the MCP protocol headers needed for the handshake/session are preserved.

### Byte-level truth (post-fix)

Forwarded header set (given the pre-fix inbound above + an injected upstream OAuth) is exactly:
`{Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version, User-Agent, Authorization=Bearer <upstream-oauth>}`.
`203.0.113.9` / `10.0.0.2` / `gw.internal` / `org/demo` are all absent.

### Oracle note

`aidefence_scan` on the raw forwarded-header blob returns `piiFound=false` — aidefence is BLIND to the
IP / topology / identity-header class (documented in prior findings). So for this leak the **byte-level
header-absence assertion is authoritative** (it checks the exact header names + leaked values are gone and the
protocol headers survive — no dependency on any detection regex).

## Verification (all green)

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q -k forward_headers   # 3 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                  # 1682 passed, 0 failed
cd ../services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"                   # 120 passed (unaffected)
```

The new `test_ext_proxy_forward_headers_strips_client_ip_and_topology` asserts every
`x-forwarded-*`/`x-real-ip`/`forwarded`/`via`/`referer` header is dropped, the leaked VALUES
(`203.0.113.9`/`10.0.0.2`/`gw.internal`/`org/demo`) are absent, and the protocol/benign headers are preserved.
The existing CHG-0033 credential-strip + upstream-OAuth-injection tests still pass.

## Scope / honesty note

Context-minimization / least-privilege improvement on the third-party egress — NOT a credential leak (creds were
already stripped). Closes a privacy / infra-topology / tenant-identity disclosure. The `ext_mcp_proxy` is limited
to allowlisted external domains, so the exposure was to allowlisted (trusted-ish, but external) third parties;
this reduces the trust required in them. Does not change the host-blocked live-stress status (items 14–20).
Partial coverage is not completion.
