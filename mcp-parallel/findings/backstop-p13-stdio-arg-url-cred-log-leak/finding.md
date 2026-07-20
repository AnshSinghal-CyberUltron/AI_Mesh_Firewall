# CHG-0107 — stdio spawn-arg log leak: URL-embedded credentials logged plaintext (both consumers)

**Change-id:** CHG-0107
**Date:** 2026-07-03
**Severity:** MEDIUM (1.4 credential leak to OPERATOR LOGS — a secret in a stdio server's args/URL landed in plaintext log lines; a real secondary leak sink, not a provider/LLM egress).
**Area:** HARDEN 1.4 (prevent MCP data leakage) — log hygiene on the stdio spawn path (scratchpad item 13; closes the CHG-0053 follow-up).
**Files:**
- `shared/ai_mesh_shared/mcp_stdio_common.py` — new `_safe_args_for_log` + `_redact_url_creds` + `_SECRET_ARG_HINTS`.
- `gateway/ai_mesh_gateway/mcp_stdio_adapter.py` — import the shared helper; mask at the spawn-log site (was RAW).
- `services/mcp-broker/sandbox-image/agent/stdio_manager.py` — import the shared helper; remove the local CHG-0053 copy.
- `services/mcp-broker/tests/test_stdio_common.py` (+12) · `services/mcp-broker/sandbox-image/agent/tests/test_stdio_manager_packages.py` (+2) · `gateway/ai_mesh_gateway/tests/test_mcp_stdio_adapter_branch.py` (+1).
**Whose work it touches:** the shared stdio-common module; the gateway `mcp_stdio_adapter` (backstop-only add — it had NO masking); the sandbox agent `stdio_manager` (deduped its CHG-0053 local copy into shared). Corrects the CHG-0053 follow-up omission.

## Root cause

The stdio-spawn log line — `LOG.info("Starting stdio MCP process: %s %s (key=%s)", command, args, key)` — logs
the child's argv for debuggability. Two defects left credentials in plaintext operator logs:

1. **Sandbox agent (`stdio_manager.py:403`)** — CHG-0053 added `_safe_args_for_log` which masks secret-**flag**
   values (`--token X` / `--api-key=X`). But a credential embedded in a URL passed as a **standalone** arg fell
   into the `else: out.append(s)` branch and was logged **verbatim**:
   - `postgres://admin:S3cr3tPass@db.internal:5432/prod`
   - `https://x-access-token:ghp_…@github.com/o/r`
   - `https://api.example.com/mcp?api_key=AKIAIOSFODNN7EXAMPLE&token=abc`
   - `mongodb://root:hunter2@mongo:27017/admin`
2. **Gateway adapter (`mcp_stdio_adapter.py:426`)** — logged `command, args, key` with **NO masking at all** —
   both the flag-secrets AND the URL creds egressed plaintext (worse than the sandbox agent).

Root cause of the divergence: two copies of the log-hygiene helper (one in the sandbox agent, none in the gateway).

### Byte-level truth (pre-fix) — driving the REAL `_safe_args_for_log` (sandbox) / raw args (gateway)

| Arg | Sandbox (CHG-0053) | Gateway (pre-CHG-0107) |
|-----|--------------------|------------------------|
| `postgres://admin:S3cr3tPass@…` | **LEAK** (standalone) | **LEAK** (no masking) |
| `https://x-access-token:ghp_…@github` | **LEAK** | **LEAK** |
| `https://…/mcp?api_key=AKIA…&token=abc` | **LEAK** | **LEAK** |
| `--token s3cr3t` | masked | **LEAK** (no masking) |

## The fix (CHG-0107)

ONE hardened `_safe_args_for_log` (+ `_redact_url_creds`) now lives in the SHARED module
`shared/ai_mesh_shared/mcp_stdio_common.py`, imported by BOTH consumers (the sandbox image vendors the shared
file via `Dockerfile: COPY shared/ai_mesh_shared/mcp_stdio_common.py …`). It masks:
- secret-**flag** values (`--token X` → `--token ***`; `--api-key=X` → `--api-key=***`), and
- **URL-embedded credentials** in every arg: the WHOLE userinfo (`scheme://user:pass@host` → `scheme://***@host`
  — the whole userinfo because a token can sit in the user OR the password position), and the values of
  secret-named **query params** (`?api_key=…&token=…&page=2` → `?api_key=***&token=***&page=2`, non-secret params
  preserved), for standalone args AND non-secret `--flag=URL` inline values.

`_redact_url_creds` is **fail-safe**: on any parse hiccup it returns the string unchanged (logging must not crash
the spawn path). The sandbox agent's local CHG-0053 copy was removed (deduped into shared) so both paths redact
identically forever.

### Byte-level truth (post-fix) — shared `_safe_args_for_log`

```
postgres://admin:S3cr3tPass@db.internal:5432/prod   -> postgres://***@db.internal:5432/prod
https://x-access-token:ghp_…@github.com/o/r          -> https://***@github.com/o/r
https://ghp_…@github.com/o/r  (token in user pos)    -> https://***@github.com/o/r
https://api.example.com/mcp?api_key=AKIA…&token=abc&page=2 -> ?api_key=***&token=***&page=2
mongodb://root:hunter2@mongo:27017/admin             -> mongodb://***@mongo:27017/admin
--dsn=postgres://u:pw_SEKRET@h/db                    -> --dsn=postgres://***@h/db
--token s3cr3t / --api-key=s3cr3t  (controls)        -> --token *** / --api-key=***
https://safe.example.com/mcp?page=2  (benign)        -> unchanged
@modelcontextprotocol/server-filesystem@1.0.0        -> unchanged
```

### Independent oracle (aidefence_scan)

`aidefence_scan` on the raw postgres spawn-log line → `piiFound: true`; on the masked line → `piiFound: false`.
aidefence is **blind** to the AWS-key / URL-query-param class (documented in prior findings), so for those the
byte-level assertion is authoritative — and notably the masking is done with `urlsplit`/string ops, **independent
of the detection regexes**, so the byte-level test does not rely on "the same regexes under test."

## Verification (all green)

```
cd services/mcp-broker && ./.venv/bin/python -m pytest tests/test_stdio_common.py -q -k "SafeArgs or RedactUrl"  # 12 passed
cd services/mcp-broker && ./.venv/bin/python -m pytest tests -q -k "not websocket"                              # 120 passed
cd services/mcp-broker/sandbox-image/agent && PYTHONPATH=…/shared ../../.venv/bin/python -m pytest tests/test_stdio_manager_packages.py -q  # 33 passed
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_stdio_adapter_branch.py -q            # 6 passed
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                             # 1663 passed, 0 failed
```

## Scope / honesty note

Closes the CHG-0053 follow-up and unifies the two divergent log-hygiene helpers into one shared source. This is a
log-sink credential leak (operator logs), not a provider/LLM egress — but operator logs ARE a secret sink and this
is a defense-in-depth 1.4 property. The sandbox-agent change takes effect on the next sandbox-image rebuild (the
Dockerfile vendors the shared file); the gateway change is live in-process. Does not change the host-blocked
live-stress status (items 14–20). Partial coverage is not completion.
