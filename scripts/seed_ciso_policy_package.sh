#!/usr/bin/env bash
# Seed CISO 100-rule package and compile to Redis for an org (default: zeroshield).
set -euo pipefail
ORG_SLUG="${1:-zeroshield}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if docker compose ps control --status running -q 2>/dev/null | grep -q .; then
  docker compose exec -T control python manage.py seed_ciso_policy_package --org-slug "$ORG_SLUG"
else
  echo "control container not running; use: docker compose up -d control" >&2
  exit 1
fi
