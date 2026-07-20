# BACKSTOP hardening — ext_mcp_proxy SSRF / DNS-rebinding guard added (CHG-0065)

- **Item:** G3 item 12 (gVisor + egress-lockdown) / SSRF ("finding mcp#1" class). Also item 9 (gateway validation).
- **Change-id:** CHG-0065 (2026-07-02)
- **Type:** SSRF (missing resolved-IP guard on a forwarding path), fail-closed.

## Gap
The tenant-facing external MCP passthrough `ext_mcp_proxy` validates the target ONLY by matching the
hostname STRING against a static allowlist (`_ALLOWED_MCP_DOMAINS` = mcp.context7.com /
api.githubcopilot.com / mcp.linear.app), then forwards to `https://{hostname}/...`. It NEVER resolves
the hostname or checks the resolved IP. So an allowlisted domain that RESOLVES to an internal address
— via DNS rebinding, DNS hijack of a third-party domain, or a misconfigured/future allowlist entry —
is forwarded to, letting a caller reach:
  * the cloud-metadata endpoint `169.254.169.254` (→ IAM credential theft),
  * loopback `127.0.0.1` / link-local `169.254.0.0/16`,
  * RFC-1918 internal services.
The sibling internal paths (`internal_discover_tools`, `internal_tools_call`) ALREADY call
`is_safe_outbound_url` for exactly this ("SSRF guard, finding mcp#1: reject internal/loopback/
link-local/cloud-metadata targets") — ext_mcp_proxy was the omission / inconsistency.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
After building `target_url`, ext_mcp_proxy now calls the same guard the internal paths use:
```
_ssrf_ok, _ssrf_reason = is_safe_outbound_url(target_url)
if not _ssrf_ok:
    return JSONResponse({"error": f"Upstream URL rejected by SSRF guard: {_ssrf_reason}"}, status_code=400)
```
`is_safe_outbound_url` (in `_url_guard.py`) validates the scheme, resolves the hostname via
`getaddrinfo`, and blocks if ANY resolved IP is private / loopback / link-local / cloud-metadata,
FAIL-CLOSED on a parse/DNS error. `MCP_ALLOW_INTERNAL_HOSTS` overrides for dev (same as the internal
paths). httpx's default `follow_redirects=False` means a 302-to-internal is returned (and response-
scanned per CHG-0061/0064), not auto-followed, so the initial-target guard is the needed control.

## No false positives / no test-hermeticity regression
Real public allowlisted domains resolve to public IPs → allowed. The existing ext-proxy redaction
tests use real allowlisted domains; the guard would make them do real DNS, so a file-scoped autouse
fixture defaults `is_safe_outbound_url` to "allow" (keeping those tests hermetic), and the dedicated
SSRF tests exercise the block path.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 45 passed. New SSRF tests: (1) when the guard rejects the resolved target → ext_mcp_proxy returns
  400 with "SSRF guard" + the reason (wiring); (2) REAL guard, allowlist patched to {"localhost"} →
  ext_mcp_proxy("localhost/mcp") resolves 127.0.0.1 → 400 (end-to-end, deterministic, no network);
  (3) a safe public resolved target is NOT SSRF-blocked (proceeds to the scan/forward path).
- Direct guard probe: `is_safe_outbound_url("https://localhost/mcp")` → (False, private 127.0.0.1);
  `...("https://169.254.169.254/...")` → (False, cloud metadata); `...("https://mcp.linear.app/mcp")`
  → (True, ok).
- Full sweep `ai_mesh_gateway/tests` → 1245 passed, 0 failed.

## Residual note
`_ALLOWED_MCP_DOMAINS` is operator-controlled (static), so the practical exposure was DNS rebinding /
hijack of a third-party domain — MEDIUM likelihood but HIGH impact (metadata credential theft). The
guard closes it and restores parity with the internal paths.
