#!/usr/bin/env bash
# Seed comprehensive PII policy package for an organization (docker compose).
# Requires a control image that includes policy/pii_policy_catalog (rebuild after pull).
set -euo pipefail
cd "$(dirname "$0")/.."

ORG_SLUG="${SEED_PII_POLICY_ORG_SLUG:-${1:-zeroshield}}"
EXTRA=()
if [[ "${2:-}" == "--reset" ]] || [[ "${RESET_PII_SEED:-}" == "1" ]]; then
  EXTRA+=(--reset)
fi

docker compose exec -T control python manage.py seed_pii_policy_package \
  --org-slug "${ORG_SLUG}" \
  "${EXTRA[@]}"
