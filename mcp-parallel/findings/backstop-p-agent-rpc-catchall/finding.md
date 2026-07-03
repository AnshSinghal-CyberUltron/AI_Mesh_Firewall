# CHG-0135 — sandbox agent /rpc leaked unexpected exceptions as raw HTTP 500 (broke JSON-RPC error contract)

**Change-id:** CHG-0135
**Date:** 2026-07-03
**Severity:** MEDIUM (reliability + defense-in-depth, chaos-recovery). An unexpected exception from a transport handler escaped the agent's `/rpc` handler as a raw HTTP 500 instead of a structured JSON-RPC error — losing the `jsonrpc_id` correlation + the broker/gateway error classification, and potentially surfacing internal exception detail. More likely under chaos (item 18): connection resets, timeouts, partial failures.
**Area:** HARDEN THE ARCHITECTURE — auto-recovery / no-leakage-during-recovery; agent robustness.
**Files:** `services/mcp-broker/sandbox-image/agent/main.py` (`/rpc` handler); `services/mcp-broker/sandbox-image/agent/tests/test_rpc_unified.py` (+1 test).
**Whose work it touches:** the owning-session in-sandbox agent `/rpc` entrypoint.

## Root cause

The agent's single `/rpc` entrypoint dispatches every transport (stdio / streamable-http / sse / websocket)
inside one `try`, but the `except` only caught **two** types:

```python
    except UpstreamError as exc:
        return _jsonrpc_error(body.jsonrpc_id, exc.code, exc.message)
    except RuntimeError as exc:
        ...
        return _jsonrpc_error(body.jsonrpc_id, -32000, str(exc))
```

Any **other** exception raised by a transport handler — `asyncio.TimeoutError`, `OSError` /
`ConnectionError`, `ValueError`, a `KeyError`, or a plain bug — **escaped** the handler → FastAPI returned a raw
**HTTP 500**. Consequences:

1. The caller (broker → gateway) got a non-200 HTTP error instead of the `{"jsonrpc":"2.0","id":<id>,"error":…}`
   envelope, so the **`jsonrpc_id` correlation is lost** and the broker's error classification degrades to a
   generic 502.
2. A raw 500 can carry framework exception detail (and would carry a stack trace if debug were ever enabled) —
   an unexpected-exception message may reference internal paths/state.
3. Under **chaos** (kill sandbox/broker/Redis/PG — item 18), unexpected exceptions (resets, timeouts, partial
   failures) are exactly the more-likely case, so recovery produced raw 500s instead of clean errors.

## The fix (CHG-0135)

A final `except Exception` safety net returns a **structured JSON-RPC error carrying the request id**, with a
**generic message** only (`"internal sandbox agent error"`, code `-32000`). The full detail is logged
server-side via `LOG.exception` with SAFE metadata (org / server / method — never params/args), so an unknown
exception's text can never leak to the caller. `asyncio.CancelledError` is a `BaseException` (not `Exception`),
so cancellation still propagates uncaught — the catch-all does not swallow task cancellation.

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_rpc_unified.py -q     # passes
../../.venv/bin/python -m pytest tests -q                        # full agent suite green
```

New `test_unexpected_exception_returns_jsonrpc_error_not_500`: patches the transport handler to raise a
`ValueError("SECRET_INTERNAL_DETAIL …")`; the `/rpc` response is HTTP 200 with `{"id":777,"error":{"code":-32000,
"message":"internal sandbox agent error"}}` — id preserved, generic message, the secret detail absent.

## Scope / honesty note

Defense-in-depth safety net for the agent's error contract; the known `UpstreamError` / `RuntimeError` paths
(with their controlled messages) are unchanged. Does not alter the host-blocked live-stress status (items
14–19). Partial coverage is not completion.
