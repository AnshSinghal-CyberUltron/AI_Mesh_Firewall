# BACKSTOP hardening — ext_mcp_proxy non-200 / non-JSON egress leak closed (CHG-0061)

- **Item:** G2 item 2 / 1.4 ("field-level redaction of tool RESULTS — byte-verified, fail-closed").
- **Change-id:** CHG-0061 (2026-07-02)
- **Type:** Real result-egress leak (unscanned forwarding), fail-closed fix.

## Gap
The tenant-facing external MCP passthrough `ext_mcp_proxy` (`/v1/mcp/ext-proxy/{host}/{path}`,
behind auth; CHG-0032/0033/0034/0043 lineage) applied its outbound result/error redaction floor ONLY
to `status_code == 200` JSON bodies. Two egress paths bypassed scanning entirely and forwarded the
untrusted external server's body RAW:
1. **NON-JSON body** — when `resp.json()` raised (an HTML error page, a `text/plain` error, an XML
   fault), the code returned `Response(content=body_bytes, ...)` verbatim. A secret/PII/infra string
   in that body (e.g. `Error: db at postgres://user:pass@10.1.2.3 unreachable; contact
   john.doe@example.com`) egressed to the tenant unscanned.
2. **NON-200 JSON body** — both scan branches were gated on `resp.status_code == 200`, so a non-200
   JSON error body (`{"error":{"message":"connect failed; reach john.doe@example.com"}}` at HTTP 500,
   or `{"detail":"user john@example.com not found on db.internal"}` at 404) fell through to
   `return JSONResponse(content=data, ...)` RAW.

This directly contradicts the CHG-0043 intent ("a JSON-RPC error response can STILL leak a secret ...
scan + mask it"), which was only wired for the 200 case.

## Fix — `gateway/ai_mesh_gateway/mcp_proxy.py`
- `import re` (was missing) + `_is_text_content_type()` / `_TEXT_CONTENT_TYPE_RE` (text/*, application/
  json|xml|javascript|x-ndjson|graphql|*+json|*+xml). A missing content-type defaults to
  application/json upstream → treated as text (safe default).
- **Non-JSON branch:** if the body is text-like, scan it via `_scan_tool_result_floor(text, ...)`
  (the same fail-closed floor) before forwarding — on a scan block/error the body is WITHHELD
  ("Response withheld: body could not be safely inspected."). Binary bodies (image/*, octet-stream)
  are passed through untouched (text-masking would corrupt them; not a text-leak vector).
- **JSON branches:** dropped the `status_code == 200` gate from the `result` and `error` scans (they
  now run on ANY status), and added an `elif resp.status_code != 200 and data is not None:` fallback
  that scans the WHOLE body for non-200 responses lacking a JSON-RPC result/error (e.g. `{"detail":
  ...}`, a list, a scalar). A 200 body without result/error is a benign session/notification shape and
  is left untouched (no behaviour change on the established path).

## No regression
- Full ext-proxy test file (existing 33) + 6 new = 39 passed; existing 200-path result/error/SSE/
  fail-closed tests unchanged.
- Binary passthrough proven byte-for-byte unchanged (image/png body).

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q`
  → 39 passed. New tests: non-200 JSON error body redacted (status preserved); non-200 detail body
  without result/error redacted; non-200 result body redacted; non-JSON text body redacted; binary
  body passed through untouched; non-JSON text body fails closed (withheld) on scan error.
- Full sweep `ai_mesh_gateway/tests` → 1220 passed, 0 failed.

## Scope note
This is the EXTERNAL passthrough path (direct httpx). The primary ORG path (`org_mcp_jsonrpc`) is
sandbox-routed via `broker_send_rpc`, which returns a parsed JSON-RPC dict scanned by the same floor,
so it never had the raw-httpx-body issue. Fix is ext-proxy-scoped.
