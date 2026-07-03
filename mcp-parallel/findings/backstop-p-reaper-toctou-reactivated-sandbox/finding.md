# CHG-0127 — reaper TOCTOU: an idle sandbox reactivated mid-sweep was still reaped (dropped its in-flight call)

**Change-id:** CHG-0127
**Date:** 2026-07-03
**Severity:** MEDIUM (soak/concurrency correctness — under load the idle reaper could stop a sandbox that a concurrent request had just reactivated, dropping that request's in-flight tool call and forcing a re-provision). Directly relevant to stress items 15/16/18 ("none dropped/mixed", "reaper correct", "no leakage during recovery").
**Area:** HARDEN THE ARCHITECTURE — CPU/mem/disk/timeout limits + reaper correctness; the idle-sandbox reaper must not race live traffic.
**Files:** `services/mcp-broker/src/sandbox/reaper.py` (`reap_idle_sandboxes`); `services/mcp-broker/src/sandbox/registry.py` (new `SandboxRegistry.is_idle`); `services/mcp-broker/tests/test_sandbox_reaper.py` (+2 tests).
**Whose work it touches:** the owning-session broker idle reaper + registry (item #24 lineage).

## Root cause

`reap_idle_sandboxes` snapshotted the idle set ONCE, then stopped entries one at a time:

```python
for entry in registry.idle_entries(cfg.idle_timeout, now=now):   # snapshot at T0
    ...
    await asyncio.to_thread(docker_manager.stop, entry.org_slug)  # <-- awaits (yields loop)
    registry.remove(entry.org_slug)
```

`await asyncio.to_thread(...)` yields the event loop, and the sweep processes N idle
entries sequentially — so the last entry is stopped long after the T0 snapshot. During
any of those awaits, a request routed to `_forward_sandbox_rpc` (routes.py) calls
`docker_manager.touch_activity(org_slug)` → `registry.touch(...)`, **reactivating** that
sandbox. But the reaper still stopped it on the basis of the **stale T0 snapshot** and
removed the registry entry. The in-flight request was then forwarded to a container the
reaper was concurrently stopping → the call **failed / was dropped**, and the next call
had to re-provision. Under the mandate's soak + high-concurrency stress this is an
intermittent dropped-call source (and a recovery churn).

## The fix (CHG-0127)

- New `SandboxRegistry.is_idle(org_slug, idle_timeout, *, now=None)` — a locked, single-entry
  idleness re-check (returns False if the entry is gone or has been touched within the window).
- `reap_idle_sandboxes` now **re-checks `is_idle` immediately before stopping each entry** and
  **skips** any sandbox reactivated since the snapshot. In production (`now=None`) the re-check
  reads a FRESH clock, so a `touch()` that landed after the snapshot is seen and the sandbox is
  spared. This collapses the race window from "the whole sweep duration" to "a single stop", and
  eliminates the dominant case (later entries in a multi-entry sweep).

This does not change the intended behavior for genuinely-idle sandboxes (still reaped) nor the
per-entry error isolation (item #24).

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_reaper.py -q     # 12 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"         # 160 passed
```

- `test_registry_is_idle_recheck` — `is_idle` flips True→False after a `touch`, and is False for a
  removed entry.
- `test_reaper_skips_sandbox_reactivated_mid_sweep` — drives the real race: the mocked `stop("a")`
  touches "b" (a request arriving during a's stop await); the reaper reaps "a" but **skips "b"**,
  `stop` is invoked for "a" only, and "b" remains registered.

## Scope / honesty note

Closes the dominant snapshot→stop TOCTOU. A residual micro-window remains between the locked
re-check and the `stop` call itself (a request arriving in those milliseconds); fully eliminating it
would need a "reaping" state flag the request path consults + re-provision-on-conflict, which is a
larger change and a rarer window — noted, not implemented here. Unit-level (mock clock/Docker); does
not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
