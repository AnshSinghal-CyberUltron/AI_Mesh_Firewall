# CHG-0086 — circuit_breaker.py: non-atomic INCR+EXPIRE (4 sites) → orphan-key window

**Change-id:** CHG-0086
**Date:** 2026-07-02
**Severity:** LOW–MEDIUM (Redis orphan-key window on mid-pipeline failure; completes the CHG-0062/0084 Redis-atomicity hardening — the documented CHG-0084 residual)
**Area:** HARDEN THE ARCHITECTURE — PostgreSQL + Redis correctness (item 11)
**Files:** `gateway/ai_mesh_gateway/circuit_breaker.py` (+ `tests/test_circuit_breaker_atomic_ttl.py`)
**Whose work it touches:** the Redis-backed LLM circuit breaker (CHG-0062/0084 lineage).

## How it was found (closing the CHG-0084 residual)

CHG-0084 fixed a non-atomic SADD+EXPIRE and flagged that `circuit_breaker.py` still used
`pipeline(transaction=False)` for INCR+EXPIRE. This closes it.

## Gap

`circuit_breaker.py` used `pipeline(transaction=False)` at **4** sites that INCR (or DELETE) + EXPIRE:
- `record_success` (total counter)
- `record_error` (total + errors counters)
- `_bump_epoch` (epoch counter + probes DELETE)
- `_admit_probe` (admit counter) — whose docstring even says *"Atomically claim a probe slot"*, yet the
  INCR+EXPIRE were not wrapped.

A non-transactional pipeline sends the commands batched but WITHOUT MULTI/EXEC; a connection drop mid-write
(INCR sent, EXPIRE not) leaves the counter key with **no TTL** → orphaned (same class as CHG-0062/0084).
The counters are per-model (bounded), so the memory impact is smaller than the per-key CHG-0084 case, but
atomic TTL-setting is the correct pattern everywhere, and `_admit_probe`'s "atomic" contract was unmet.

## Fix

All 4 pipelines now use `pipeline(transaction=True)` → the INCR (+DELETE) and its EXPIRE commit atomically
via MULTI/EXEC, so the counter can never be left without a TTL. `execute()` still returns the per-command
results (record_error reads `results[0]`/`results[2]`, `_admit_probe` reads `results[0]`) — verified by the
existing 24 circuit-breaker tests.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_circuit_breaker_atomic_ttl.py -q   # 1 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q -k "circuit or breaker"              # 24 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                       # 1454 passed, 0 failed
```
`grep pipeline(transaction=False) circuit_breaker.py` → NONE. Broker regression
`services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- All gateway INCR/SADD+EXPIRE Redis writes on the MCP/rate-limit/breaker paths are now atomic
  (CHG-0062 rate-limit, CHG-0084 leakage-detector, CHG-0086 circuit-breaker). `mcp_oauth_proxy`/`model_state`
  use `setex`/`set(ex=)` (atomic by construction); no remaining non-atomic TTL-setter found.
