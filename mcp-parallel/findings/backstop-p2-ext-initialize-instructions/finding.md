# CHG-0080 — ext proxy forwarded the MCP `initialize` result (model-facing `instructions`) UNSCANNED

**Change-id:** CHG-0080
**Date:** 2026-07-02
**Severity:** MEDIUM (a malicious external MCP server's initialize `instructions` — model-facing, "analogous to a system prompt" — plus serverInfo reached the model unscanned on the transparent proxy: tool-poisoning / metadata-leak)
**Area:** HARDEN 1.4 — scan coverage of model-facing untrusted-upstream MCP content
**Files:** `gateway/ai_mesh_gateway/mcp_proxy.py` (+ `tests/test_mcp_ext_initialize_scan.py`)
**Whose work it touches:** the external MCP proxy path (`ext_mcp_proxy`); routes initialize through the existing result floor (CHG-0074..0079).

## How it was found (devil's-advocate — model-facing surfaces)

After scanning tool descriptions (CHG-0077), the next model-facing MCP surface: the `initialize` result's
**`instructions`** field. The MCP spec treats `instructions` as guidance for the LLM ("can be used to
improve the LLM's understanding … thought of like a hint … may be added to the system prompt"). So it is a
tool-poisoning / indirect-prompt-injection + metadata surface exactly like tool descriptions.

## Gap

- ORG path (`org_mcp_jsonrpc`, method == "initialize"): **safe** — it SYNTHESIZES a gateway initialize
  response (`serverInfo` = "ZeroShield Gateway", no upstream `instructions` forwarded), so nothing untrusted
  reaches the model.
- EXT path (`ext_mcp_proxy`, the transparent external proxy): scans a result only when the method is in
  `_EXT_FINITE_RESULT_METHODS` — and `initialize` was **NOT** in that set. So the upstream's initialize
  response (including the model-facing `instructions` and `serverInfo`) was forwarded **RAW, unscanned**. A
  malicious external server could put an injection/exfil instruction or a hidden secret/internal-IP in
  `instructions` and it reached the model past the firewall.

## Fix

Add `"initialize"` to `_EXT_FINITE_RESULT_METHODS`. The handshake result is finite (small), so it is safe to
buffer + scan. This routes the initialize result through the same result-redaction floor as tool results,
inheriting the whole chain: CHG-0074 (IP-network floor), CHG-0075 (credential-exposure), CHG-0076
(text-encoding exfil block), CHG-0079 (invisible/confusable-unicode deobfuscation), and injection
detection/tagging (CHG-0078). initialize ARGS (client capabilities/clientInfo) are NOT scanned — client-
provided, not sensitive.

## Behaviour after fix (verified)

- Secret + internal-IP in initialize `instructions` → **masked**, tagged `INFRA, SECRET`.
- Zero-width-hidden secret in `instructions` → **blocked** fail-closed (CHG-0079).
- Injection in `instructions` → detected (`prompt_injection` finding, tagged/audited).
- Benign initialize (`"Use search() to find documents…"`) → unchanged, no error.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_ext_initialize_scan.py -q   # 5 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                    # 1358 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- Injection in `instructions` is detected+tagged but (per the CHG-0078 residual) not removed under the
  default `tag` posture — same output-injection enforcement question deferred there.
- Other one-shot metadata methods (`resources/templates/list`, `logging/setLevel` responses, etc.) are
  either already covered or carry no model-facing free text; revisit if new model-facing fields appear.
