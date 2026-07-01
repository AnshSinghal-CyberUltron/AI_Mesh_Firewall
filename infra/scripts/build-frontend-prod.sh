#!/usr/bin/env bash
# Production Vite build — VITE_* URLs are compile-time; run on laptop/CI before sync-to-ec2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -f .env ]] || die "Missing .env — copy .env.ec2.sample and fill in values."

# shellcheck disable=SC1091
set -a && source .env && set +a

# Container-only cache paths from .env/docker-compose must not leak into local npm ci.
unset NPM_CONFIG_CACHE UV_CACHE_DIR XDG_CACHE_HOME

export VITE_FRONTEND_BASE_URL="${VITE_FRONTEND_BASE_URL:-${FRONTEND_ORIGIN:-https://aimeshfirewall.zeroshield.ai}}"
export VITE_BACKEND_BASE_URL="${VITE_BACKEND_BASE_URL:-${BACKEND_PUBLIC_URL:-https://aimeshbackend.zeroshield.ai}}"
export VITE_GATEWAY_SAME_ORIGIN="${VITE_GATEWAY_SAME_ORIGIN:-false}"
export VITE_GATEWAY_BASE_URL="${VITE_GATEWAY_BASE_URL:-${GATEWAY_PUBLIC_URL:-https://aimeshgateway.zeroshield.ai}}"

echo "==> Building frontend (VITE_FRONTEND_BASE_URL=${VITE_FRONTEND_BASE_URL})"
cd "${ROOT}/frontend"

if command -v npm >/dev/null 2>&1; then
  npm ci
  npm run build
else
  # npm not on PATH (common on Windows/Git Bash) — run the build inside a Node container.
  echo "    npm not found on PATH; building via Docker (node:20-alpine)"
  command -v docker >/dev/null 2>&1 || die "Neither npm nor docker is available — install Node.js or Docker."
  # MSYS_NO_PATHCONV=1 prevents Git Bash from mangling /app → C:/Program Files/Git/app.
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" docker run --rm \
    -v "$(cygpath -m "${ROOT}/frontend"):/app" \
    -w "/app" \
    -e "VITE_FRONTEND_BASE_URL=${VITE_FRONTEND_BASE_URL}" \
    -e "VITE_BACKEND_BASE_URL=${VITE_BACKEND_BASE_URL}" \
    -e "VITE_GATEWAY_SAME_ORIGIN=${VITE_GATEWAY_SAME_ORIGIN}" \
    -e "VITE_GATEWAY_BASE_URL=${VITE_GATEWAY_BASE_URL}" \
    node:20-alpine \
    sh -c "npm ci && npm run build"
fi

[[ -f dist/index.html ]] || die "frontend build did not produce dist/index.html"
echo "Built frontend/dist/ ($(du -sh dist | awk '{print $1}'))"
