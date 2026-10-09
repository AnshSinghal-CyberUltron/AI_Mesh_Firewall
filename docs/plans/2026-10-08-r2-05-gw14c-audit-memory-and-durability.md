# R2-05 / GW14c — Audit durability and the store memory budget

**Status:** implemented, local half CLOSED. **Card:** GW14c. **Severity:** CRITICAL (v3-blocking).
**Date:** 2026-10-08.
**Evidence:** `docs/plans/evidence/2026-10-08-r2-05/`.

---

## 1. Executive summary

Audit shares the hot store with the state the gateways enforce from, and audit streams have no
TTL. Round 2 measured the consequence live: **1.48 → 6.42 GiB in 51 minutes on a 10.4 GiB
instance.** The cap in force (`MAXLEN 2,000,000` per org, ≈2.7 KB per record, so ≈5.4 GiB *per
tenant*) was not set wrong — it was the wrong *kind* of quantity. A record count cannot bound
memory.

What fails when the store fills is not audit:

1. Under an evicting policy (`volatile-lru` is Memorystore's default) the **guard-owner
   registrations are evicted first**, because they are the only keys with a TTL. Discovery reads
   0/N and new gateways never become ready.
2. Every write is then refused — audit, the lease counters, **and the control plane's publishes**.
3. So a kill switch or key revocation commits to Postgres and is never published. The fleet keeps
   serving the previous snapshot, which is still signed and still valid.

`noeviction` alone does not fix it: the heartbeats are refused, so the registrations expire
instead of being evicted, and publishes are still refused.

This card lands three mechanisms, and corrects one defect in the reference design it was told to
follow.

| # | Mechanism | Where |
|---|---|---|
| 1 | A global audit byte budget turned into a per-org approximate `MAXLEN`, with an exact trim counter | `gateway_v2/audit/budget.py`, `gateway_v2/runtime/store_audit.py` |
| 2 | A store-policy self-check and memory gauges sampled off the request path | `gateway_v2/runtime/storemem.py` |
| 3 | A durable Postgres sink with an acknowledged-vs-durable high-water mark and exact `records_lost` | `audit_control/` |

**Correction to the reference.** `rc3-audit-mem-v1` models a stream entry as `payload × 1.10 +
256 B` and its README states the model was above the real cost at every size it sampled. It was —
but it does not generalise, and re-measuring against Valkey 8 across a wider range shows why:

| payload | `used_memory` Δ/entry | ratio | factor needed over a 256 B flat term |
|---|---|---|---|
| 315 B | 379 B | 1.21 | 0.39 |
| 512 B | 590 B | 1.15 | 0.65 |
| 1,024 B | 1,204 B | 1.18 | 0.93 |
| **2,048 B** | **2,587 B** | **1.26** | **1.14** |
| 2,900 B | 3,092 B | 1.07 | 0.98 |
| **4,096 B** | **5,147 B** | **1.26** | **1.19** |
| 6,000 B | 6,171 B | 1.03 | 0.99 |
| **8,192 B** | **10,267 B** | **1.25** | **1.22** |

The real cost oscillates with the allocator's size classes rather than rising smoothly: a payload
landing just above a class costs the next class up. The reference sampled 2.9 KB and 6 KB, which
are two of the cheap points. At 2 KB, 4 KB and 8 KB the factor 1.10 **under-charges**, and an
under-charging model derives a `MAXLEN` that is too long — so the store fills while every gauge
reports the bound being honoured. `ENTRY_FACTOR` is now **1.35**, which covers the worst measured
case with ~10% headroom, and the live gate asserts the model stays above both `used_memory` Δ and
`MEMORY USAGE` at all eight sizes.

**Correction to my own first design.** The first version derived `records_lost` from the writer's
`XTRIM` count. That is wrong in the case that matters most: a FLUSH removes the already-durable
records too, so it would have reported 100 lost where 36 were lost. Loss is now the card's own
subtraction — `acknowledged − durable − pending` — which is exact in every case.

---

## 2. What was built

### 2.1 The budget (`gateway_v2/audit/budget.py`)

```
MAXLEN(org) = budget / (tenants × store_bytes_per_record(org))   clamped to [100, stream_maxlen]
```

* **`budget`**: `AMF_AUDIT_STORE_BUDGET_MB` when set, else `AMF_AUDIT_STORE_FRACTION` (0.5) ×
  the store's `maxmemory`, re-read every 10 s so a resize is followed without a restart, else
  the per-org `AMF_AUDIT_STREAM_MAXLEN` alone **with a warning** — that last case is the RC2
  configuration, and it is returned as a warning rather than passing as a configuration.
* **`tenants`**: every org in the plan snapshot, including idle ones. Conservative on purpose: a
  cap that expands while tenants are quiet has to contract — by trimming — exactly when traffic
  arrives and the store is under most pressure.
* **`store_bytes_per_record` is PER ORG** (payload EWMA × 1.35 + 256 B). Dividing a byte budget
  by a fleet-average record size gives every org an equal *record* share, so a tenant with 10×
  larger records takes 10× the memory. Per-org sizing gives each an equal *byte* share.

The knobs accept both the `AMF_` spelling (this tree's convention) and the `RV_AUDIT_*` names the
card and the reference use; `AMF_` wins when both are set.

### 2.2 Trim, not cap (`gateway_v2/runtime/store_audit.py`)

`XADD` carries **no** `MAXLEN`. A separate `XTRIM … approximate` per touched stream does the
bounding, because **`XTRIM` returns how many entries it removed** and `XADD` does not report what
it displaced. That single return value is the whole of `audit_trimmed_records`, and it is the
direct answer to R2-11's M3 finding. An AST-level test asserts `XADD` never grows a `maxlen`
kwarg, so an "optimisation" that moves the cap back onto the append fails a gate rather than
passing review.

### 2.3 The posture check and the gauges (`gateway_v2/runtime/storemem.py`)

`INFO memory` + `INFO stats`, with `CONFIG GET` only as a fallback — Memorystore permits `INFO`
and restricts `CONFIG`, so a check built on `CONFIG GET maxmemory-policy` reads empty exactly
where it matters. Never fatal: an unreachable store at start-up logs and falls back, because
refusing to start would turn a store blip into a fleet outage. The eviction finding is logged at
ERROR **with the remediation in the message**, including that `noeviction` alone does not work.

`MemorySampler` runs on worker 0 only, inside the audit writer's task group so it is cancelled
with it, two small round trips per node per 10 s, off the request path.

### 2.4 The producer (`gateway_v2/audit/sink.py`, `record.py`)

`emit()` is `put_nowait` and nothing else: no await, no raise, no store contact. The queue is
bounded, so loss is possible, so it is counted — `produced`, `dropped`, `written`, `failed`.

Two deliberate namings:

* The sink publishes **`acknowledged_ratio`**, never "completeness". Acknowledged means the store
  took it; a record the store took can still be trimmed before anything durable has it.
  Conflating the two IS the M3 defect, and a test asserts no attribute on the sink's counters
  contains the word.
* The queue depth has **no default**. GW14's card states the bound as *"the contract's
  `queue_depth()` at the writer's measured drain rate"*, so a new
  `ResourceContract.audit_queue_depth(drain_rate, bytes_per_record)` derives it — bounded by time
  (drain rate × stall budget) and by memory (2% of the worker's usable limit / bytes per record),
  smaller wins. The existing `queue_depth()` is deliberately not reused: its slots are in-flight
  *requests*, bounded by `per_worker_rss`, which would size a few-KB record queue at tens.

**`Finding.evidence` is never serialized.** It is the detector's matched text; an audit stream is
append-only, exported to a durable sink, and retained, so evidence in a record would put the
user's secret at rest in three places and undo the redaction the pipeline just performed.
Detector, version, category, status, confidence and span *offsets* are kept — offsets locate a
finding without reproducing it.

### 2.5 C40: sheds and admission rejects

A shed is a decision, not an absence of one, and it goes through the same producer, budget and
sink as a served request. Each carries a **reason** (R2-11's M2: `rejected_503` merged 9,241
kill-switch 503s with 8 store-outage 503s) and its `retry_after_ms` (R2-08). Completeness is
measured against **admitted** requests, which is what makes an unrecorded shed visible — the
prototype's ratio read 1.0 while up to 5.75% of admitted requests were shed without a record,
because a missing record is missing from both halves of a served-requests fraction.

### 2.6 Durability (`audit_control/`)

```
lost = acknowledged − durable − pending
```

* **`acknowledged`** is pushed by the writer (`AuditSink.acknowledged_by_org()`), because the
  information is destroyed by the thing it measures: once a record is trimmed, nothing in the
  store remembers it existed.
* **`durable`** is what Postgres holds.
* **`pending`** is what is still in the stream above the durable cursor — zero after a complete
  drain, so the answer is exact; bounded by the stream length when a round was truncated, which
  makes the ratio a *lower* bound. A ratio that over-states completeness is the defect.

The page insert and the cursor advance commit in **one transaction**. If only the page landed, the
re-read is harmless — `ON CONFLICT (org_id, stream_id) DO NOTHING`. If only the cursor landed, the
records would be skipped and the loss would read zero, so the asymmetry is deliberately on the
safe side.

Stream ids are parsed into `(ms, seq)` pairs before any comparison. `'10-1' < '9-1'` as text, so a
string comparison would make loss detection skip a decade of ids silently and report zero in
exactly the case this card exists for.

### 2.7 The process (`python -m audit_control`)

R2-04's shape, clause for clause, because R2-05 has the same requirement-with-no-component
problem: `from_env` as a pure function of a mapping, `verify_bounds()` fatal before the first
round, `require_bounded_client` on the store, `ThreadingHTTPServer` for `/healthz` + `/metrics`
(no `/readyz` — an exporter has no readiness semantics), and **the package copied into the
image**, asserted by a test, because R2-04 found `state_control` declared in `pyproject` and
present in no image.

### 2.8 The sink choice, priced

The card says *"GCS, BigQuery or Postgres; choose and price at GW14c"*. **Postgres.**

* Cloud SQL PostgreSQL 16 HA is already in the signed fleet (4 vCPU / 16 GiB / 100 GiB,
  **$526.40/mo**, §0.2).
* `state_control/pg.py` already carries the R2-04-hardened bounded sessions, so the audit sink
  inherits `SET LOCAL` authority, the client-side `tcp_user_timeout`, the keepalives and
  `verify_bounds()` rather than re-declaring four numbers in a second place.
* Round 2 measured its failure behaviour: failover 11.6–15.5 s loaded, **0 acknowledged writes
  lost**. GCS and BigQuery have no adapter in this repository and no measured failure behaviour
  here.

**Storage arithmetic**, stated as inputs rather than as a conclusion, because the rate is not yet
signed: at `R` records/s and ~2.9 KB per record, audit accrues `R × 2.9 KB/s` ≈ `R × 245 GB/day`
per thousand records/s. At the current 100 RPS Poisson pass point with ~2 records per request
(input + output phase), that is ≈ **49 GB/day**, so the existing 100 GiB instance holds ≈2 days
and the sink needs its own disk sizing plus a retention policy before GW20. **This is a decision
for the owner**, and it is the one number in this card that cannot be settled without the signed
capacity: the choice of Postgres is made, the disk size is not.

---

## 3. Verification

### 3.1 Gates (all green)

| Gate | Result |
|---|---|
| `pytest` (offline) | **814 passed**, 76 skipped, 1 xfailed |
| `pytest` live Valkey 8 (`AMF_LIVE_VALKEY=1`) | **13 passed** (byte model × 8 sizes, XTRIM exactness, convergence, flood, negative control, live resize) |
| `pytest` live Postgres 16 (`AMF_PG_DSN=…`) | **13 passed** (schema applies + idempotent, page-plus-cursor in one transaction, `ON CONFLICT` suppresses a re-insert, body stored as exact bytes, parsed-id ordering, malformed payload survives, exporter end to end with durable loss) |
| L14c-1 F-AUDIT-MEM, 4 arms | **4 passed** |
| `mypy --strict` | clean, 125 source files |
| `ruff` | clean |
| `import-linter` | 2 contracts kept, 0 broken |
| AST gates × 3 trees | clean (`gateway_v2`, `state_control`, `audit_control`) |

The AST capacity gate caught a real design error during the work — a literal queue depth in
`audit/sink.py` — which is why the bound now comes from the `ResourceContract`.

### 3.2 L14c-1 — F-AUDIT-MEM, measured

64 MB private Valkey 8, 70,000 records at ~2.9 KB (≈3.4× `maxmemory`), 5 TTL-keyed owners
heartbeating, real `ValkeyPublisher.publish_kind` probed throughout.

| arm | regs | evicted | refused publishes | refused audit writes | trimmed | used |
|---|---|---|---|---|---|---|
| **bounded, noeviction** | 5/5 | 0 | **0** | **0** | 65,732 | 13.9 / 64 MiB |
| **bounded, volatile-lru** | 5/5 | 0 | **0** | **0** | 65,732 | 13.9 / 64 MiB |
| unbounded, noeviction *(control)* | 5/5 | 0 | **4** | **50,000** | 0 | 64.0 / 64 MiB |
| unbounded, volatile-lru *(control)* | 5/5 | **23** | 0 | **50,000** | 0 | 64.0 / 64 MiB |

The control arms reproduce the chain: the store fills, writes are refused — **including control
plane publishes** — and under the evicting policy keys are evicted. The bounded arms hold at 22%
of `maxmemory` with every trimmed record counted. Comparable to the reference's table (RC2:
51,600 of 72,400 refused; patched: 65,508 trimmed, 20.8 MiB).

### 3.3 L14c-3 — the flush

100 records produced, 64 exported, store flushed. `records_lost = 36`, exactly, and
`completeness_ratio = 0.64`. Under the behaviour R2-11 measured, that same sequence read **1.0**.

---

## 4. Open, and honestly partial

| Item | Why it is not closed here |
|---|---|
| **L14c-2** — 1 h at the fleet's Poisson knee; memory flat below the alarm, 0 evictions, durable sink complete against admitted requests | Needs the fleet lane and a serving gateway. `gateway_v2/edge/app.py` is still a stub (GW12), and R2-01 requires the knee be Poisson-signed at fleet scale first. |
| **L14c-1's "0 refused lease refills"** | `gateway_v2/admit/quota.py` is a stub — no `TokenLease`, no lease Lua. **Not asserted**, rather than asserted vacuously. GW19. |
| **L14c-1's "5/5 registrations" in the CONTROL arms** | The registration victim is a declared surrogate (`SET … PX 3000`), because `detect/guard/discovery.py` does not exist (GW08). My harness re-heartbeats immediately before each probe, so the expiry window the reference measured (0/5 after 20 s at TTL 3 s) does not bite. The chain IS reproduced — 23 evictions and 50,000 refused writes — but "registrations 0/5" specifically is not. |
| **The gateway half of the alert rules** | `deploy/observability/gw14c-audit-memory-alerts.yml` ships with the card because the thresholds are part of the design, and is deliberately absent from `rule_files`: nothing serves `amf_audit_*` until GW12/GW14. The exporter's own half is usable as soon as it is deployed. |
| **Nothing emits an audit record yet** | The mechanisms are complete and proven, but `gateway_v2/edge/app.py` is a stub, so no request reaches `emit()`. This card deliberately built only the slice of GW14 its own bullets and tests require; the timing instrument (`T_fw_addon`, the signed residual and its CI gate, LGW14-1…6, the logging publisher) is GW14's and depends on GW12. Until those land, GW14c is a bound around a path that carries no traffic. |
| **The sink's disk size and retention** | §2.8. The choice is made; the sizing needs the signed capacity. Owner decision. |
| **`audit_control` is outside the import-linter layer contract** | `root_packages = ["gateway_v2"]`, so the contract covers the gateway's internal layering only. `state_control` is in the same position and set the precedent; both are covered by `mypy --strict`. Deliberate, recorded rather than fixed. |
| **Tenant discovery for the exporter** | `AMF_AUDIT_ORGS` is configuration. Discovering tenants by scanning the store would be a `SCAN` of the whole keyspace every round — the shape R2-02 measured at 405–459 ms with 25,000 tenants. Wire it to the plan snapshot with GW05c. |

**Found, not fixed:** `state_control/valkey.py:100` trips `check_tenant_scale` (`zrange`, a
whole-collection read). Pre-existing, on the re-hydrator's deliberately O(records) deep-verify
path, and documented as such in `rehydrate.py`. Not touched by this card.

---

## 5. Files

**New.** `gateway_v2/domain/audit_knobs.py`, `gateway_v2/runtime/storemem.py`,
`gateway_v2/runtime/store_audit.py`, `gateway_v2/audit/budget.py`,
`gateway_v2/audit/{record,sink,metrics}.py` (were stubs), `audit_control/` (`__init__`,
`__main__`, `schema`, `cursor`, `sink_pg`, `export`, `service`),
`deploy/observability/gw14c-audit-memory-alerts.yml`, and nine test modules under
`gateway_v2/tests/{audit,audit_control,runtime}/`.

**Modified.** `gateway_v2/runtime/store_keys.py` (audit namespace),
`gateway_v2/runtime/resources.py` (`audit_queue_depth`), `state_control/pg.py` (`cursor_tx()`, the
seam that shares the verified bounds), `gateway_v2/Dockerfile`, `gateway_v2/pyproject.toml`.
