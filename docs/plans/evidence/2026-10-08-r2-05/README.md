# R2-05 / GW14c evidence — 2026-10-08

Plan: `docs/plans/2026-10-08-r2-05-gw14c-audit-memory-and-durability.md`

| File | What it is |
|---|---|
| `entry-bytes-model.json` | The measurement that corrected the reference patch's byte model (1.10 → 1.35). Eight payload sizes against a real Valkey 8, `used_memory` delta and `MEMORY USAGE` per entry. |
| `l14c-1-bounded-noeviction.json` | **L14c-1 PASS.** 64 MB store, 70,000 records (~3.4× maxmemory), bound on. |
| `l14c-1-bounded-volatile-lru.json` | **L14c-1 PASS** under Memorystore's default policy. |
| `l14c-1-unbounded-noeviction.json` | Negative control: store fills, 50,000 audit writes AND 4 control-plane publishes refused. |
| `l14c-1-unbounded-volatile-lru.json` | Negative control: store fills, 23 keys evicted, 50,000 writes refused. |

Reproduce:

```bash
cd gateway_v2
AMF_LIVE_VALKEY=1 .venv/bin/python -m pytest \
  tests/runtime/test_lgw14c_live_docker.py \
  tests/audit_control/test_lgw14c_f_audit_mem.py -q -p no:randomly
```

Every live test starts and removes its own private `valkey/valkey:8-alpine` on loopback and
FLUSHALLs it. Never point them at a shared store.

Open items — including **L14c-2** (1 h at the fleet's Poisson knee), the lease-refill probe
(GW19) and the real guard registration (GW08) — are listed in §4 of the plan.
