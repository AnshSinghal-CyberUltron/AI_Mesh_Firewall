#!/usr/bin/env python3
"""Recommend Postgres max_connections for this machine from the cgroup detector +
measured per-worker DB-connection rates. Dynamic, no magic static: a 6c box needs a
different ceiling than a 12c box, derived from the same detector the servers use.

Connection model (grounded in measurement — see docs/perf/CHANGELOG PERF-0006):
  * control: Django sync views serialize on the thread-sensitive executor, so a
    worker holds only ~2–3 DB connections (CONN_MAX_AGE=60). Measured 15 conns for
    6 workers ≈ 2.5/worker; we budget 4/worker for safety.
  * gateway: talks Redis on the hot path; the only Postgres consumer is the embedding
    vault pool (GATEWAY_VAULT_POOL_MAX per worker, lazy). Budget its max.
  * celery workers/beat (workers profile) + ops (psql, admin, superuser_reserved,
    co-tenant harnesses) get a flat reserve.

recommended = workers*(control_per_worker + vault_pool) + celery_reserve + ops_margin

Both control and gateway scale to `workers` on the same box, so a single `workers`
drives both terms. Env overrides: PG_CONTROL_PER_WORKER, PG_CELERY_RESERVE,
PG_OPS_MARGIN, RESOURCE_BUDGET_CPUS (to model another profile).

Usage:
  python3 scripts/perf/pg_budget.py [--current 400]
"""

from __future__ import annotations

import argparse
import json
import os

from ai_mesh_shared import resource_budget as rb

CONTROL_PER_WORKER = int(os.environ.get("PG_CONTROL_PER_WORKER", "4"))
CELERY_RESERVE = int(os.environ.get("PG_CELERY_RESERVE", "15"))
OPS_MARGIN = int(os.environ.get("PG_OPS_MARGIN", "50"))


def budget(b: rb.ResourceBudget) -> dict:
    control = b.workers * CONTROL_PER_WORKER
    gateway_vault = b.workers * b.vault_pool
    recommended = control + gateway_vault + CELERY_RESERVE + OPS_MARGIN
    return {
        "cpu_budget": round(b.cpu_budget, 2),
        "workers": b.workers,
        "vault_pool": b.vault_pool,
        "control_conns": control,
        "gateway_vault_conns": gateway_vault,
        "celery_reserve": CELERY_RESERVE,
        "ops_margin": OPS_MARGIN,
        "recommended_max_connections": recommended,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--current", type=int, default=None,
                    help="deployed max_connections to check the recommendation against")
    args = ap.parse_args(argv)

    b = rb.detect()
    out = budget(b)
    if args.current is not None:
        out["current_max_connections"] = args.current
        out["sufficient"] = args.current >= out["recommended_max_connections"]
        out["headroom"] = args.current - out["recommended_max_connections"]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
