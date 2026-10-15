# Declared store-failure behaviour (B8 / R2-13, partial)

This is the authoritative, operator-readable statement of what happens when the state store fails,
and which drills certify each behaviour. It is co-located beside the `state_control` component it
documents because the subject is the store-failure semantics of the shipped `state_control` twins
and re-hydrator specifically.

All measured figures below are the **C36** facts from the backend-rewrite runbook. The local flush
drill (`gateway_v2/tests/state_control/test_flush_drill_r2_13.py`) asserts the **correctness** of the
flush → restore path (0 Restore_Violations, a deterministic one-round bound), **not** any wall-clock
figure — the in-memory twins are not comparable to a live Valkey.

## 1. Flush → re-hydrator restore

A store flush (`FLUSHALL`) wipes the **published copy** — manifests, records, index, engaged sets,
and stamp. The **durable Postgres control plane is the source of truth and is untouched** by a flush.
One re-hydrator round reads the durable truth and republishes every kind through the whole-kind
`publish_kind` restore path (the re-hydrator-only path), bringing the store back with **0
Restore_Violations**.

C36 measured facts:

- `FLUSHALL` restore timings: **0.456 s / 1.249 s / 0.610 s**, bound **≈ 2.05 s**.
- Store flush run **×3**, each restored in **0.46–1.25 s**.
- A **10 s re-hydrator gap** produced only **declared 503s** and **0 violations in all 15 runs**.

The local drill `test_flush_drill_r2_13.py` asserts the **correctness** — 0 Restore_Violations and a
deterministic **one-round** bound (expressed as a re-hydrator round count) — and deliberately does
**not** assert the ≈ 2.05 s wall-clock figure (see the drill table in §5).

## 2. Cloud SQL failover — no data-plane impact

A Cloud SQL (durable control plane) failover has **no data-plane impact** under C36:

- **0 × 503** to tenants.
- **0 acknowledged writes lost.**

## 3. Valkey planned-maintenance failover

A Valkey **planned-maintenance** failover costs **≈ 140 declared 503s** (C36). These are declared,
bounded, and recoverable — the data plane returns an honest 503 rather than silently serving stale or
lost state.

## 4. Valkey has no manual failover

Valkey **cannot be failed over manually**. A data-loss failover drill therefore requires either
**Redis Standard HA** or a **flush drill**. The flush drill is exactly what the local drill
(`test_flush_drill_r2_13.py`) provides: it wipes the published copy while the durable Postgres truth
survives, then certifies the re-hydrator restore.

## 5. Drill delivery — local vs deferred

| Drill | Status | Owner / gate |
|---|---|---|
| Flush drill (store wiped, Postgres untouched → re-hydrator restore, 0 violations) | **Delivered locally** | `gateway_v2/tests/state_control/test_flush_drill_r2_13.py` |
| Partition drill | **Deferred** (needs real stores) | G-13 |
| Redis forced-failover drill | **Deferred** (needs real stores) | G-13 |
| Cloud SQL + store overlap drill | **Deferred** | G-14 (owned by GW05b) |
| ≈ 2.05 s wall-clock flush-restore certification | **Deferred** (cloud figure, not the local round-count bound) | T21 |
