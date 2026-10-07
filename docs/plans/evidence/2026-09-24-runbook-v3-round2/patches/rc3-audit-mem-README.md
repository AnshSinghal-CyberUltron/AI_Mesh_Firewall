# rc3-audit-mem-v1.patch: bounding audit-stream memory (H1 / SP7) for RC3

Lane r2-fix-d2, for proto-builder (the RC3 integrator). Source findings: reviewer-hidden-failures **H1** and reviewer-state-propagation **SP7** (`SP/evidence/reviewer-hidden-failures/r2-pass1.md`, `SP/evidence/reviewer-state-propagation/r2-pass1.md`).

- **Base:** RC2, `SP/evidence/r2-impl/rc2/rvproto2-rc2.tar.gz` (manifest `6093c8dd10bd12f5d5058a963ac9db3ed9768985c4907d73d3a5272c01cff0fc`, verified).
- **Patch:** `SP/evidence/r2-impl/patches/rc3-audit-mem-v1.patch`, sha256 `2ae538a5a0ebe0c60a23f7b17b3e7e2feefbab4dd2895eb0ffa270e5625da818` (also in `rc3-audit-mem-v1.patch.sha256`). 7 files: 4 modified, 3 new.

## Applying it

```bash
cd <tree> && patch -p1 --dry-run < SP/evidence/r2-impl/patches/rc3-audit-mem-v1.patch && patch -p1 < SP/evidence/r2-impl/patches/rc3-audit-mem-v1.patch
```

- **On a fresh RC2 extraction:** it applies cleanly, and the result is identical to the tested work tree (`SP/rc3am/work`).
- **On proto-builder's live `SP/rvproto2` (13:09Z):** it applies with offsets in `runtime/config_v2.py` only (new knobs above ours) and no fuzz (`rc3/live-apply.txt`). The live tree plus the patch passes **157/157** unit tests (`SP/evidence/r2-fix-d2/rc3/unit-suite-live-plus-patch.txt`).
- **Scope:** the audit write path (`audit/sink.py`, where the XADD actually is; `runtime/store.py` is untouched), its caller (`edge/state.py`), the knobs (`runtime/config*.py`) and a new startup self-check module (`runtime/storemem.py`). **Nothing** in `control/*`, `admit/state_v2.py`, `detect/` or `egress/`.

## The defect, reproduced on RC2

Audit streams (`rv:audit:<org>`, `RV_AUDIT_STREAM_MAXLEN` 2,000,000 per org, about 2.9 KB per record) never expire. The guard-owner registrations (`SET … PX 3000`) are the only TTL keys. When audit fills the store, the failure chain is:

1. **Eviction.** Under Memorystore's default `volatile-lru`, the registrations are evicted first, so discovery sees 0/N and new gateways never become ready.
2. **Refused writes.** Every write is then refused: audit XADD, lease-counter Lua, and the control plane's publishes.
3. **Unenforced kill switch.** A kill switch or revocation commits to Postgres but is never published. Gateways keep serving the old snapshot, which is still valid.

**Functional test F-AUDIT-MEM.** Every row below uses a private local `valkey/valkey:8.0` container (Valkey 8.0.11, 64 MB maxmemory, loopback only, removed after each run):
- 5 owners heartbeat every 1 s through `discovery.OwnerRegistration`.
- 2.9 KB `DecisionRecord`s are sent at 4,000/s for 20 s: 72,000–72,800 records (time-driven), about 3.4× maxmemory.
- Every 0.5 s the test probes registrations, `writer.publish_safe`, a `LEASE_LUA_V2` refill and INFO.
- At the end, a kill switch for org-b is written, then read back as a gateway would.

| Tree | Policy | Registrations (min alive) | Evicted keys | Refused publishes / lease refills | Audit writes refused | Peak used_memory | Kill switch | Verdict |
|---|---|---|---|---|---|---|---|---|
| RC2 | volatile-lru | **0/5** (evicted as the store filled) | 5 (= the registrations) | **27 / 27** (every probe after the store filled) | 51,600 of 72,400 | 64.0 MiB (full) | `ok_publish_pending`, **not enforced** | **FAIL** |
| RC2 | noeviction | **0/5** (heartbeats refused; keys expired one TTL later) | 0 | **27 / 27** | 51,600 of 72,400 | 64.0 MiB (full) | `ok_publish_pending`, **not enforced** | **FAIL** |
| RC2 + patch | volatile-lru | 5/5 | 0 | 0 / 0 | 0 of 72,000 (65,508 trimmed, counted) | 20.8 MiB (33 %) | `ok`, enforced | **PASS**; `store_policy_unsafe=1` + ERROR log |
| RC2 + patch | noeviction | 5/5 | 0 | 0 / 0 | 0 of 72,800 (66,308 trimmed, counted) | 20.8 MiB (33 %) | `ok`, enforced | **PASS**; `store_policy_unsafe=0` |

**`noeviction` alone does not fix H1.** Once audit fills the store, the heartbeats are refused and the registrations expire instead of being evicted, and publishes are still refused. The bound is the fix. `noeviction` is defence in depth: under `allkeys-*`, durable state could itself be evicted, and with `noeviction` an overrun fails loudly instead of silently removing keys.

Raw data: `SP/evidence/r2-fix-d2/rc3/f_audit_mem-{rc2,patched}-{volatile-lru,noeviction}.{json,stderr,env.txt}`. Each JSON carries the per-probe timeline. The runner is `rc3/run_f_audit_mem.sh`.

## What the patch changes

### (1) Audit bound (`audit/sink.py`, `runtime/storemem.py`)

**Budget.** One memory budget covers ALL audit streams:
- `RV_AUDIT_STORE_BUDGET_MB` when set;
- otherwise `RV_AUDIT_STORE_FRACTION` (0.5) × the store's `maxmemory`, read at startup and re-read every 10 s, so a store resize is followed;
- otherwise (`maxmemory` 0 or unreadable) the previous per-org `RV_AUDIT_STREAM_MAXLEN`, with a log line saying so.

**Per-org cap.** Each batch XADDs, then XTRIMs every stream it touched to:

`MAXLEN ~ budget / (tenants × store_bytes_per_record(org))`, clamped to [100, `RV_AUDIT_STREAM_MAXLEN`]

- **tenants** = the orgs in the plan snapshot. That is conservative: idle tenants reserve a share too.
- **store_bytes_per_record** is tracked **per org** (payload EWMA × 1.10 + 256 B). Each org's stream therefore holds an equal byte share of the budget, whatever its record size.
- The model stays above measured Valkey 8 costs at every size tested. For the 2.9 KB records it gives 3,445 B, against 3,112 B of `used_memory` delta and 3,335 B of `MEMORY USAGE` (`rc3/stream-entry-bytes.txt`: 315 B to 6 KB payloads, the model ≥ both every time).
- XADD itself carries no MAXLEN, so the trimming lives in XTRIM. **XTRIM returns what it removed**, so `audit_trimmed_records` is exact (reviewer-observability M3).

### (2) Startup self-check (`runtime/storemem.startup_check`, called before the first audit write)

- It reads **INFO memory + INFO stats**, which Memorystore allows. `CONFIG GET maxmemory-policy` is only a fallback, because Memorystore restricts `CONFIG`.
- Every worker logs a `store_memory_check` line (maxmemory, policy, used, evicted, the audit budget and its source).
- For any `volatile-*` or `allkeys-*` policy it logs `store_policy_unsafe` with `"severity": "ERROR"` (Cloud Logging reads this field) and sets the gauge `store_policy_unsafe=1`.
- It also logs an ERROR when an explicit budget is > 90 % of `maxmemory`, and a warning when `maxmemory` is 0.
- It is never fatal: an unreachable store only logs `store_memory_check_failed`.

### (3) Memory gauges (`runtime/storemem.MemorySampler`)

- **Worker 0 only**, so there is one series per node. It runs every `RV_STORE_MEMORY_SAMPLE_S` (10 s) inside the audit writer's task (a TaskGroup, cancelled with it). It is off the request path and costs 2 small INFO round trips per node per 10 s.
- It exports `store_used_memory_bytes`, `store_maxmemory_bytes`, `store_memory_used_ratio`, `store_evicted_keys` and `store_policy_unsafe`.
- A failing sample is counted (`store_memory_sample_errors`) and logged once per run of failures. It never stops the writer.

## New knobs (`V2_DEFAULTS`, logged under `settings.v2.audit`)

| Knob | Default | Meaning |
|---|---|---|
| `RV_AUDIT_STORE_BUDGET_MB` | empty | Store memory ALL audit streams may use. Empty means the fraction below × `maxmemory`. **Set it explicitly on a shared instance** (see USAGE) |
| `RV_AUDIT_STORE_FRACTION` | 0.5 | Share of `maxmemory` for audit when no explicit budget is set; must be in (0, 0.9] |
| `RV_STORE_MEMORY_SAMPLE_S` | 10 | INFO memory/stats sampling period for the gauges (worker 0) |
| `RV_AUDIT_STREAM_MAXLEN` | 2,000,000 | Unchanged: now the per-org **upper** bound |

## New metrics

| Metric | Meaning |
|---|---|
| `rv_audit_trimmed_records_total` | Records XTRIM removed to hold the budget (exact) |
| `rv_audit_budget_bytes` | Current audit budget (0 = none known) |
| `rv_audit_stream_maxlen` | Per-org MAXLEN of the most constrained stream in the last batch |
| `rv_audit_bytes_per_record` | Largest per-org store-bytes estimate in the last batch |
| `rv_store_used_memory_bytes`, `rv_store_maxmemory_bytes`, `rv_store_memory_used_ratio`, `rv_store_evicted_keys` | From INFO, worker 0, every 10 s |
| `rv_store_policy_unsafe` | 1 = evicting policy (volatile-* / allkeys-*) |
| `rv_store_memory_sample_errors_total` | Failed samples |

## For USAGE.md

- **§2:** add the three knobs.
- **Store settings:** run Memorystore with **`maxmemory-policy noeviction`** (a Memorystore configuration parameter) **plus the audit bound**. With the bound, audit leaves `1 - fraction` of `maxmemory` (default 50 %) for durable state, registrations and client buffers.
- **Shared instance** (several namespaces or lanes on one Valkey): the default fraction is per gateway fleet, so set `RV_AUDIT_STORE_BUDGET_MB` per namespace so that all budgets together stay ≤ about 50 % of `maxmemory`.
- **Alerting:** `rv_store_policy_unsafe == 1`; `rv_store_memory_used_ratio > 0.7`; any increase of `rv_store_evicted_keys`; `rv_audit_trimmed_records_total` rising faster than the audit export/spool drains (audit is being lost from the store).
- **§5:** add the metrics above.

## Limits

- **Durability.** The bound protects the store; it does not make audit durable. Trimmed records are gone from the store, and they are counted. The durable path is the planned audit spool (C41). Until then, export must drain faster than the trim rate.
- **Offboarded orgs.** Streams of orgs that no longer write (offboarded tenants) are never trimmed again. Export and UNLINK them.
- **Upgrade over existing streams.** Approximate XTRIM removes at most about 10,000 entries per call (Redis/Valkey default LIMIT), so a stream far above its new MAXLEN at upgrade converges over successive batches. Trim or export it first if the store is near full.
- **Budget arithmetic is per worker.** Every worker derives the same MAXLEN for an org from the same inputs, and small differences only make the store trim to the smallest.

## Tests

| Test | RC2 | RC2 + patch |
|---|---|---|
| `tests/functional/f_audit_mem.py` (private Valkey 8, see above) | **FAIL** (volatile-lru and noeviction) | **PASS** (volatile-lru and noeviction) |
| `tests/unit/test_audit_memory.py`: 18 tests covering the policy check (all 8 policies), INFO/CONFIG parsing, budget derivation, the unlimited-store and oversized-budget warnings, equal byte shares, exact trim count, sampler gauges and survival, TaskGroup cancellation, knob validation | collection error (module absent) | 18 passed |
| Full unit suite, incl. GW00 gates + import-linter | 136 passed | **154 passed** |
| Live `SP/rvproto2` (13:09Z) + patch | — | **157 passed** |

- **Unit tests:** `cd <tree> && RV_GATEWAY_V2_PATH=<gateway_v2> RV_GUARD_TOKENIZER=<tokenizer> .venv/bin/python -m pytest tests/unit`.
- **Functional test:** `SP/evidence/r2-fix-d2/rc3/run_f_audit_mem.sh <tree> volatile-lru OUT.json`. The runner starts and removes its own container. Any private Valkey also works: `RV_TREE=<tree> python tests/functional/f_audit_mem.py redis://…`. It FLUSHALLs, so never point it at a shared store.
