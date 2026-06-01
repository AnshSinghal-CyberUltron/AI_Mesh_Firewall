#!/usr/bin/env bash
# Bootstrap ZeroShield org admin for AI Mesh Firewall (docker compose).
set -euo pipefail
cd "$(dirname "$0")/.."

EXTRA_ARGS=()
if [[ "${ZEROSHIELD_ADMIN_PASSWORD:-}" != "" ]]; then
  EXTRA_ARGS+=(--password "$ZEROSHIELD_ADMIN_PASSWORD")
fi
if [[ "${1:-}" == "--reset-password" ]]; then
  EXTRA_ARGS+=(--reset-password)
fi

docker compose exec control python manage.py ensure_zeroshield_admin "${EXTRA_ARGS[@]}"
