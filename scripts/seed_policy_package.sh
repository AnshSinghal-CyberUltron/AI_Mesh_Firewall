#!/usr/bin/env bash
# Seed the organization policy package (50+ policies across pipeline/rag/mcp/vector,
# each a category with multiple rules) for an organization, via docker compose.
#
# Requires a control image that includes policy/policy_package (rebuild after pull):
#   docker compose build control && docker compose up -d control
#
# Usage:
#   bash scripts/seed_policy_package.sh <org-slug> [--reset]
#   SEED_POLICY_PACKAGE_ORG_SLUG=acme bash scripts/seed_policy_package.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ORG_SLUG="${SEED_POLICY_PACKAGE_ORG_SLUG:-${1:-zeroshield}}"
EXTRA=()
if [[ "${2:-}" == "--reset" ]] || [[ "${RESET_POLICY_PACKAGE_SEED:-}" == "1" ]]; then
  EXTRA+=(--reset)
fi

docker compose exec -T control python manage.py seed_policy_package \
  --org-slug "${ORG_SLUG}" \
  "${EXTRA[@]}"
