#!/usr/bin/env bash
# GW05c phase 6 — fault drills on one machine.
#
# Provisions a throwaway Postgres and Valkey, runs every suite that can be verified locally
# (including the pause-based partition and outage drills), writes an evidence verdict, and tears
# the containers down. It touches nothing pre-existing: both containers are named amf-gw05c-* and
# are removed on exit, including on failure.
#
# What it CANNOT do, and does not pretend to: L05c-1. The card's acceptance test needs a lane —
# a gateway VM with 12 workers, an L4 guard, Cloud SQL, Memorystore, two re-hydrators and two
# load generators — and measures C4 p99 at 1k/10k/25k tenants. Nothing here measures latency;
# every assertion is a count of commands, records, rounds or round trips.
#
#   usage: scripts/gw05c_local_drills.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${REPO}/gateway_v2"
VENV="${WORKSPACE}/.venv/bin"
PG_NAME="amf-gw05c-pg"
KV_NAME="amf-gw05c-valkey"
PG_PORT="${AMF_DRILL_PG_PORT:-55432}"
KV_PORT="${AMF_DRILL_KV_PORT:-56379}"
EVIDENCE="${REPO}/docs/plans/evidence/$(date +%Y-%m-%d)-gw05c"

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
    "card": "GW05c",
    "phase": "6 (local fault drills)",
    "postgres": "${PG_VERSION}",
    "valkey": "${KV_VERSION}",
    "gates": "5 AST x 2 trees + tenant-scale + import-linter + ruff + mypy --strict: clean",
    "pytest_offline": counts("pytest-offline.txt"),
    "pytest_live": counts("pytest-live.txt"),
    "drills": counts("drills.txt"),
    "drilled": [
        "store flush -> fail closed -> rehydrate -> serve",
        "regressed store refused against an applied floor",
        "unpublished write invisible until a rehydrator round",
        "store partition (docker pause) bounded by the op timeout, then heal",
        "postgres outage does not fail a store round; rehydrator reports per kind",
        "rehydration completes under a held FOR UPDATE (R2-04)",
        "60 concurrent writers leave a dense gap-free position sequence",
        "G-04 cold start: 2,000 keys over 200 orgs, one engaged scope read",
        "bulk onboard of 25,000 records in bounded rounds",
        "steady round at 25,000 tenants reads zero records",
    ],
    "not_drilled_needs_lane": {
        "L05c-1": "C4 p99 at 1k/10k/25k x phases A-D; needs 1 gw x 12 workers + L4 guard",
        "sp1/sp1-live": "a lagging REPLICA and a promotion",
        "sp2": "fail-closed depends on GW05b's signed freshness stamp (not implemented)",
        "valkey_forced_failover": "not offered by Memorystore at all (R2-13)",
    },
    "latency_claims": "none. every assertion is a count, not a duration",
}
print(json.dumps(verdict, indent=2))
PY

echo "==> evidence written to ${EVIDENCE}"
cat "${EVIDENCE}/verdict.json"
