#!/usr/bin/env bash
# Production Vite build — VITE_* URLs are compile-time; run on laptop/CI before sync-to-ec2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -f .env ]] || die "Missing .env — copy .env.ec2.sample and fill in values."

# shellcheck disable=SC1091
set -a && source .env && set +a

export VITE_FRONTEND_BASE_URL="${VITE_FRONTEND_BASE_URL:-${FRONTEND_ORIGIN:-https://aimeshfirewall.zeroshield.ai}}"
export VITE_BACKEND_BASE_URL="${VITE_BACKEND_BASE_URL:-${BACKEND_PUBLIC_URL:-https://aimeshbackend.zeroshield.ai}}"
export VITE_GATEWAY_SAME_ORIGIN="${VITE_GATEWAY_SAME_ORIGIN:-false}"
export VITE_GATEWAY_BASE_URL="${VITE_GATEWAY_BASE_URL:-${GATEWAY_PUBLIC_URL:-https://aimeshgateway.zeroshield.ai}}"

echo "==> Building frontend (VITE_FRONTEND_BASE_URL=${VITE_FRONTEND_BASE_URL})"
cd "${ROOT}/frontend"
npm ci
npm run build

[[ -f dist/index.html ]] || die "frontend build did not produce dist/index.html"
echo "Built frontend/dist/ ($(du -sh dist | awk '{print $1}'))"
