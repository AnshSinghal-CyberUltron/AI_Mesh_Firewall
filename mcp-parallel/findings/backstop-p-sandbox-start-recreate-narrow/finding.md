# CHG-0133 — a dead sandbox that fails to start was never recreated (auto-recovery gap)

**Change-id:** CHG-0133
**Date:** 2026-07-03
**Severity:** MEDIUM (availability / chaos self-heal — a per-org sandbox left in a dead/OOM-killed/corrupted state after a chaos kill would fail EVERY request for that org with no auto-recovery until manual intervention). Directly relevant to stress items 13/18 (auto-recovery; chaos kill sandbox → recover).
**Area:** HARDEN THE ARCHITECTURE — auto-recovery / self-heal (sandbox).
**Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`_start_or_recreate`); `services/mcp-broker/tests/test_sandbox_lifecycle.py` (+2 tests).
**Whose work it touches:** the owning-session broker sandbox lifecycle (`ensure` / `start` recovery path).

## Root cause

`ensure()` (and `start()`) recover a non-running sandbox via `_start_or_recreate`. Despite the name, it only
**recreated** the container for TWO specific start errors and **re-raised** everything else:

```python
def _start_or_recreate(self, org_slug, container):
    try:
        container.start()
        return container
    except Exception as exc:
        if not self._is_container_name_conflict(exc) and "marked for removal" not in str(exc).lower():
            raise                                   # <-- every other start failure
        return self._recover_broken_container(org_slug, container)
```

The two handled errors (`409 name conflict`, `"marked for removal"`) are **removal-race** conditions. But a
container left **dead / OOM-killed / corrupted** after a chaos kill fails `start()` with a *generic* OCI/APIError —
which is NOT a removal race — so it was **re-raised, not recreated**. `ensure` then propagates the error and the
NEXT request hits the same broken container, tries `start()` again, fails again → the org's sandbox is stuck
broken, failing every request with **no self-heal**. This is exactly the chaos-recovery scenario (kill a sandbox,
expect auto-recovery on the next call).

## The fix (CHG-0133)

`_start_or_recreate` now removes + recreates the container on **ANY** `start()` failure — which is the whole
point of "or_recreate". This is safe:

- The per-org auth volume (`/data/mcp-auth`) **persists** across the remove+recreate, so there is no data loss.
- A genuine **daemon-down** situation still surfaces: `create_container` would also fail (and has its own retry
  loop), so recreating can't make daemon-down worse — it fails there anyway, as before.
- A transient start failure now self-heals instead of wedging the org's sandbox.

## Verification

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q     # 44 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"           # 162 passed
```

New tests: `test_ensure_recreates_container_on_generic_start_failure` — a dead container whose `start()` raises a
generic `RuntimeError` is removed (`remove(force=True)`) and recreated (`containers.run` called), and `ensure`
returns the fresh running container; `test_ensure_does_not_recreate_when_start_succeeds` — a stopped container
whose `start()` succeeds is returned as-is (no remove, no recreate). The start-recovery path had **no tests**
before (the narrow condition was untested).

## Scope / honesty note

Broadens auto-recovery to any start failure (removal-race behavior is a subset, still handled). Unit-level (mock
Docker SDK); the true end-to-end chaos-recovery at scale is host-blocked (items 14–19). Partial coverage is not
completion.
