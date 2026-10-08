#!/usr/bin/env bash
# GW05b phase 7 — freshness fault drills on one machine.
#
# Provisions a throwaway Postgres and Valkey, runs every suite that can be verified locally, and
# tears the containers down. Named amf-gw05b-* and removed on exit, including on failure, so it
# touches nothing pre-existing.
#
# What it CANNOT do, and does not pretend to:
#
#   L05b-1  needs a lagging REPLICA and a promotion. The drills substitute a partial restore and
#           a rolled-back manifest, which reproduce the SAME class (a store generation older than
#           what a stamp attests) but not the failover itself.
#   L05b-2  the mechanism is drilled; "every request 503" needs a serving surface, and
#           gateway_v2/edge/ is still stubs. Blocked on GW06.
#   L05b-4  Cloud SQL and a loadgen. A PAUSED container reproduces the shape (accepts the
#           connection, never answers); "0 x 503 at 60% load" does not exist here.
#   L05b-5  Memorystore maintenance cannot be triggered, and Valkey offers no manual failover
#           at all (R2-13). `docker pause` is the closest available analogue.
#   L05b-6  drilled per process. "Global" needs a fleet.
#
# Where a bound IS measured, it is measured on an injected clock: the fault (a stopped database,
# a paused store, a held row lock) is real, the 16 s of waiting is not. Durations reported below
# are therefore about ORDERING and BOUNDS, never about latency.
#
#   usage: scripts/gw05b_local_drills.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${REPO}/gateway_v2"
VENV="${WORKSPACE}/.venv/bin"
PG_NAME="amf-gw05b-pg"
KV_NAME="amf-gw05b-valkey"
PG_PORT="${AMF_DRILL_PG_PORT:-55432}"
KV_PORT="${AMF_DRILL_KV_PORT:-56379}"
EVIDENCE="${REPO}/docs/plans/evidence/$(date +%Y-%m-%d)-gw05b"

cleanup() {
  docker rm -f "${PG_NAME}" "${KV_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> provisioning throwaway containers"
cleanup
docker run -d --rm --name "${PG_NAME}" -e POSTGRES_PASSWORD=pg \
  -p "${PG_PORT}:5432" postgres:16-alpine >/dev/null
docker run -d --rm --name "${KV_NAME}" \
  -p "${KV_PORT}:6379" valkey/valkey:8-alpine >/dev/null

for _ in $(seq 1 60); do
  if docker exec "${PG_NAME}" pg_isready -q 2>/dev/null \
    && docker exec "${KV_NAME}" valkey-cli ping >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
docker exec "${PG_NAME}" pg_isready -q
docker exec "${KV_NAME}" valkey-cli ping >/dev/null

PG_VERSION="$(docker exec "${PG_NAME}" postgres --version | awk '{print $3}')"
KV_VERSION="$(docker exec "${KV_NAME}" valkey-cli info server \
  | tr -d '\r' | awk -F: '/^valkey_version/{print $2}')"
echo "    postgres ${PG_VERSION} on :${PG_PORT}, valkey ${KV_VERSION} on :${KV_PORT}"

export AMF_PG_DSN="postgresql://postgres:pg@127.0.0.1:${PG_PORT}/postgres"
export AMF_VALKEY_URL="redis://127.0.0.1:${KV_PORT}/0"
export AMF_DRILL_PG_CONTAINER="${PG_NAME}"
export AMF_DRILL_VALKEY_CONTAINER="${KV_NAME}"

cd "${WORKSPACE}"

echo "==> structural gates"
for tree in gateway_v2 state_control; do
  "${VENV}/python" -m lint.check_sizes "${tree}"
  "${VENV}/python" -m lint.check_http_outside_edge_resolve "${tree}"
  "${VENV}/python" -m lint.check_no_module_mutable "${tree}"
  "${VENV}/python" -m lint.check_capacity_literals "${tree}"
  "${VENV}/python" -m lint.check_frozen_dataclasses "${tree}"
done
"${VENV}/python" -m lint.check_tenant_scale gateway_v2
"${VENV}/lint-imports" >/dev/null
"${VENV}/ruff" check gateway_v2 state_control lint tests >/dev/null
"${VENV}/mypy" --strict >/dev/null
echo "    gates, import-linter, ruff, mypy: clean"

mkdir -p "${EVIDENCE}"

echo "==> suite without infrastructure (the CI shape)"
env -u AMF_PG_DSN -u AMF_VALKEY_URL -u AMF_DRILL_PG_CONTAINER -u AMF_DRILL_VALKEY_CONTAINER \
  "${VENV}/python" -m pytest -q --junitxml="${EVIDENCE}/junit-offline.xml" \
  | tee "${EVIDENCE}/pytest-offline.txt" | tail -2

echo "==> suite with real postgres + real valkey"
"${VENV}/python" -m pytest -q --junitxml="${EVIDENCE}/junit-live.xml" \
  | tee "${EVIDENCE}/pytest-live.txt" | tail -2

echo "==> fault drills (verbose)"
"${VENV}/python" -m pytest tests/state_control/test_lgw05c_drills.py -v \
  | tee "${EVIDENCE}/drills.txt" | tail -4

echo "==> freshness suites (verbose)"
"${VENV}/python" -m pytest -v \
  tests/runtime/test_lgw05b_stamp_sig.py \
  tests/runtime/test_lgw05b_stamp_view.py \
  tests/runtime/test_lgw05b_cadence_metrics.py \
  tests/admit/test_lgw05b_failclosed.py \
  tests/state_control/test_lgw05b_rehydrate_stamp.py \
  tests/state_control/test_lgw05b_pg_grace.py \
  | tee "${EVIDENCE}/freshness.txt" | tail -4

echo "==> negative controls (each must FAIL the paired test)"
"${REPO}/scripts/gw05b_negative_controls.sh" \
  | tee "${EVIDENCE}/negative-controls.txt" | tail -12

"${VENV}/python" - <<PY > "${EVIDENCE}/verdict.json"
import json
import re
from pathlib import Path

evidence = Path("${EVIDENCE}")


def counts(name: str) -> dict[str, int]:
    text = (evidence / name).read_text(errors="replace")
    found = {}
    for label in ("passed", "failed", "skipped", "error"):
        hit = re.search(rf"(\d+) {label}", text)
        if hit:
            found[label] = int(hit.group(1))
    return found


verdict = {
    "card": "GW05b",
    "phase": "7 (live stack + local fault drills)",
    "closes": ["R2-03 (SP1, SP2)", "R2-04 gateway half"],
    "postgres": "${PG_VERSION}",
    "valkey": "${KV_VERSION}",
    "gates": "5 AST x 2 trees + tenant-scale + import-linter + ruff + mypy --strict: clean",
    "pytest_offline": counts("pytest-offline.txt"),
    "pytest_live": counts("pytest-live.txt"),
    "drills": counts("drills.txt"),
    "freshness_suites": counts("freshness.txt"),
    "drilled": [
        "SP1: a FRESH worker refuses a rolled-back store, across both real adapters",
        "SP1 control: the same worker WITHOUT a stamp serves the rollback",
        "SP2: an unpublished commit fails the gateway closed within the bound",
        "SP2 control: the original GW05c behaviour, pinned",
        "two re-hydrators, 50 interleaved rounds: verified_at monotonic (R2-04)",
        "a forged stamp in a real store never wedges freshness",
        "a rotated signing secret fails the reader closed",
        "store flush takes the stamp too; fail closed, then restore AND re-stamp",
        "store partition (docker pause): freshness ages out, then recovers",
        "paused postgres: fresh through the grace, closed past it, recovers on unpause",
        "both re-hydrators gone: fail closed at the bound, recover after one returns",
        "flush under a held FOR UPDATE: restored and stamped while the lock is held",
    ],
    "measured_locally": {
        "fail_closed_after_verification_stops_s": "5.0-5.3 (one freshness bound)",
        "recovery_after_one_rehydrator_returns_s": "<= 0.2",
        "freshness_recovery_after_a_store_partition_s": "< 5.0",
        "rehydration_under_a_held_row_lock_s": "< 5.0 (RC2: 38 s)",
        "paused_postgres_operation_cost_s": "5.0 at shipped bounds, 2.0 at 1 s bounds",
    },
    "not_drilled_needs_lane_or_gw06": {
        "L05b-1": "a lagging REPLICA and a promotion; drills substitute a partial restore",
        "L05b-2": "mechanism drilled; 'every request 503' needs edge/ (GW06)",
        "L05b-3": "drilled locally; the reference figure (0.54-0.79 s) is Cloud SQL's",
        "L05b-4": "Cloud SQL failover at 60% load; shape drilled with docker pause",
        "L05b-5": "Memorystore maintenance cannot be triggered; no manual failover (R2-13)",
        "L05b-6": "drilled per process; 'global' needs a fleet",
    },
    "uncalled_pending_gw06": [
        "state_ready (/readyz must call it)",
        "require_bounded_client (start-up must call it)",
        "StateKnobs (no env loader; GW06 owns the start-up path)",
        "next_delay_s / is_deep_round (no loop driver exists)",
    ],
    "clock_note": (
        "every measured bound uses an injected clock. the FAULT is real (stopped database, "
        "paused store, held row lock); the waiting is not. no latency is claimed."
    ),
}
print(json.dumps(verdict, indent=2))
PY

echo "==> evidence written to ${EVIDENCE}"
cat "${EVIDENCE}/verdict.json"
