#!/usr/bin/env bash
# Launch the REAL dev-profile gateway under uvicorn (serve.main) as a background process.
# Task 37 (v2-enforcement-leaks). The dev profile reads every AMF_DEV_* var from this process env.
# Budget mode = lease (real GW06 over Redis) so AMF_DEV_BUDGET_POOL_TOKENS is exercised.
set -euo pipefail

cd "$(dirname "$0")/../../../../.." # -> repo root
cd gateway_v2

export AMF_DEV_PROFILE=1
export AMF_DEV_API_KEY="dev-key-task37"
export AMF_DEV_ORG_ID="dev-org"
export AMF_DEV_PLAN_PATH="dev/plan.example.json"
export AMF_DEV_MODELS="echo-1"
export AMF_DEV_Q_SAFE="500"
export AMF_DEV_AUDIT_DRAIN_RATE_PER_S="1000"
export AMF_DEV_AUDIT_BYTES_PER_RECORD="2765"
export AMF_DEV_BUDGET="lease"
export AMF_DEV_REDIS_URL="redis://127.0.0.1:6399/0"
# Pool sized to cover every uvicorn worker's lease chunk (9 workers x 3,276,800 token cap).
export AMF_DEV_BUDGET_POOL_TOKENS="100000000"
export AMF_DEV_RATE_PER_S="100000"
export AMF_DEV_BURST="100000"
export AMF_DEV_LEASE_TTL_S="300"

export RV_HOST="127.0.0.1"
export RV_PORT="8399"

exec .venv/bin/python -m gateway_v2.edge.serve
