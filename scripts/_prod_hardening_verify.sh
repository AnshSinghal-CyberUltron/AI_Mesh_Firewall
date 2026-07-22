#!/usr/bin/env bash
# Post-deploy acceptance checks for Module 1.5 + Demo Files hardening.
set -euo pipefail

cd "$(dirname "$0")/.." || exit 1
COMPOSE="${COMPOSE:-docker compose -f docker-compose.yml -f docker-compose.prod.yml}"

FH="${FIREWALL_HOST:-aimeshfirewall.zeroshield.ai}"
BH="${BACKEND_HOST:-aimeshbackend.zeroshield.ai}"
GH="${GATEWAY_HOST:-aimeshgateway.zeroshield.ai}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

echo "=== Layer: public health ==="
curl -sf "https://${FH}/gw-health" >/dev/null || fail "UI gw-health"
curl -sf "https://${BH}/api/health/" >/dev/null || fail "backend health"
curl -sf "https://${GH}/health" >/dev/null || fail "gateway health"
pass "public health endpoints"

echo "=== Layer: demo (optional Basic Auth) ==="
if [[ -n "${DEMO_AUTH_USER:-}" && -n "${DEMO_AUTH_PASSWORD:-}" ]]; then
  curl -sf -u "${DEMO_AUTH_USER}:${DEMO_AUTH_PASSWORD}" \
    "https://${FH}/demo/api/health" >/dev/null || fail "demo /api/health"
  pass "demo health via nginx"
else
  echo "SKIP: DEMO_AUTH_USER/PASSWORD not set"
fi

echo "=== Layer: gateway routing pool ==="
if [[ -n "${DEMO_GATEWAY_KEY:-}" ]]; then
  models_json="$(curl -sf -H "Authorization: Bearer ${DEMO_GATEWAY_KEY}" "https://${FH}/v1/models" || true)"
  echo "${models_json}" | grep -q '"data"' || fail "v1/models missing data"
  pass "v1/models with demo key"
else
  echo "SKIP: DEMO_GATEWAY_KEY not set"
fi

echo "=== Layer: nginx upstream freshness reminder ==="
echo "If any service was force-recreated, run: \$COMPOSE up -d --force-recreate nginx"

echo "=== All configured checks passed ==="
