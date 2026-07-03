# CHG-0131 — ws transport silently enforced the websockets 1 MiB default, ignoring _MAX_RESPONSE_BYTES

**Change-id:** CHG-0131
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (correctness + policy-enforcement consistency). The WebSocket transport did not honor the agent's configured response-size cap: a **raised** cap was ignored (legit 1–8 MiB ws responses failed) and, more importantly, a **lowered** operator cap was **under-enforced** (ws still accepted up to 1 MiB). Also improves OOM safety (library-level pre-buffer bound at the intended size).
**Area:** HARDEN THE ARCHITECTURE — CPU/mem/timeout limits; consistent per-transport response-size enforcement (parity with the http/sse/stdio `_MAX_RESPONSE_BYTES` caps).
**Files:** `services/mcp-broker/sandbox-image/agent/ws_manager.py` (`ensure_ws_connected`); `services/mcp-broker/sandbox-image/agent/tests/test_upstream_proxy.py` (+1 test).
**Whose work it touches:** the owning-session in-sandbox agent WebSocket upstream proxy.

## Root cause

`ensure_ws_connected` opened the upstream WebSocket with:

```python
ws = await asyncio.wait_for(
    websockets.connect(session.url, additional_headers=extra_headers, open_timeout=connect_timeout),
    timeout=connect_timeout,
)
```

No `max_size` was passed, so `websockets` (v16.0) applied its **default `max_size = 1 MiB`** (1048576). The agent's
intended response cap is `_MAX_RESPONSE_BYTES` (**8 MiB** default, env `MCP_AGENT_MAX_RESPONSE_BYTES`), which every
other transport honors (http/sse per-response, stdio line). Consequences on the ws transport ONLY:

1. A message between 1 MiB and 8 MiB was rejected by the library (`PayloadTooBig` → surfaced as a WS
   connection-closed error) even though the agent's policy allowed it — legit large ws responses failed.
2. The explicit `if len(raw) > _MAX_RESPONSE_BYTES` check (post-`recv`) was **dead code**: the library rejected
   anything over 1 MiB before `recv()` ever returned an 8 MiB payload.
3. If an operator **lowered** `MCP_AGENT_MAX_RESPONSE_BYTES` below 1 MiB (tighter policy), the ws transport still
   accepted up to 1 MiB — the tightened cap was **not enforced** on ws (a real under-enforcement).

## The fix (CHG-0131)

Pass `max_size=_MAX_RESPONSE_BYTES` to `websockets.connect()`. Now the library enforces the agent's configured
cap — the same value as the other transports — and does so **before buffering the whole frame** (real pre-buffer
OOM guard, stronger than the post-`recv` length check). The explicit length check remains as belt-and-suspenders.

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_upstream_proxy.py -q -k "websocket or ws_connect"   # ws tests pass
../../.venv/bin/python -m pytest tests -q                                                        # full agent suite green
```

New `test_ws_connect_enforces_response_cap_via_max_size` patches `websockets.connect` and asserts it is called
with `max_size == _MAX_RESPONSE_BYTES`. Existing ws tests (`test_websocket_tools_list`, handshake-401) still pass
(their `AsyncMock` accepts the new kwarg). Confirmed empirically that websockets 16.0 `connect()` defaults
`max_size` to 1048576 (1 MiB), vs the agent's 8 MiB.

## Scope / honesty note

Aligns the ws response cap with the agent's configured `_MAX_RESPONSE_BYTES` for consistent, honored per-transport
enforcement. Not a data-leak fix. Does not change the host-blocked live-stress status (items 14–19). Partial
coverage is not completion.
