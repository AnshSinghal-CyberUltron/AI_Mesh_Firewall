# BACKSTOP finding — sandbox-agent leak-to-logs audit + mask secrets in the stdio args log (CHG-0053)

- **Item:** G4 item 13 (monitoring/logging hygiene) + 1.4 (prevent MCP data leakage).
  Extends the CHG-0041 gateway-logging audit to the broker + sandbox agent (prompted by
  the CHG-0052 broker logging).
- **Change-id:** CHG-0053 (2026-07-02)
- **Severity:** LOW — secret-to-(operator)-logs hardening. Config-dependent (only if a
  credential is passed as a stdio arg — the normal location is `env`, which is NOT logged).

## Audit — does the broker / sandbox agent log MCP tool data (PII/secrets)?
Swept every log statement in `services/mcp-broker/src/` and
`services/mcp-broker/sandbox-image/agent/`:
- **Tool-call RESULTS / params: NEVER logged** — the 1.4-critical property holds. The
  only server RESULT logged is the `initialize` result (`stdio_manager.py:509`,
  `json.dumps(result)[:200]`) — capabilities/serverInfo, not user PII, and `json.dumps`
  escapes control chars so no log injection; ACCEPTABLE, left as-is.
- Broker `_forward_sandbox_rpc` (CHG-0052) logs metadata only (org/server/transport/
  method/jsonrpc_id/request_id) — clean.
- stderr/notification/non-JSON logs are DEBUG/WARNING diagnostics, truncated — server
  output, not tool results.
- **`stdio_manager.py:360`** logged `command` + `args` verbatim at INFO. `env` (the
  normal secret store) is never logged, but a credential passed as a stdio ARG
  (`--token XYZ` / `--api-key=XYZ`) would land in operator logs in plaintext.

## Fix
New `_safe_args_for_log(args)` in `stdio_manager.py`: masks the VALUE of any
secret-looking flag (`token`/`key`/`secret`/`password`/`passwd`/`auth`/`credential`/
`apikey`) — `--token XYZ` → `["--token", "***"]`, `--api-key=XYZ` → `["--api-key=***"]`.
The start-of-process log line now logs `_safe_args_for_log(args)`. Standalone positional
values (URLs, package specs) are left intact so they aren't corrupted; flag-based
secrets are the target (URL-embedded credentials are a separate, out-of-scope vector).

## Verification
- `cd services/mcp-broker && PYTHONPATH="$PWD/src:$PWD/../../shared:$PWD/sandbox-image/agent" .venv/bin/python -m pytest sandbox-image/agent/tests/test_stdio_manager_packages.py -q`
  → 31 passed, incl. 5 new `TestSafeArgsForLog` cases (value after a secret flag masked;
  inline `--api-key=…` masked; password/auth masked; non-secret args untouched; standalone
  positional not masked). No test depends on the log format.
- Broker suite `tests/` → 106 passed, 0 failed.

## Follow-ups (documented)
- URL-embedded credentials in a standalone arg (`https://user:token@host`) are not masked
  by the flag heuristic — a separate hardening if needed.
- OTEL/Jaeger + backup remain infra. Item 13 stays `[ ]`.
