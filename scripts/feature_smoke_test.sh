#!/usr/bin/env bash
# Smoke test all AI Mesh Firewall module APIs (control + gateway).
set -euo pipefail
BASE="${CONTROL_URL:-http://127.0.0.1:8100}"
GW="${GATEWAY_URL:-http://127.0.0.1:8300}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"

pass=0
fail=0
check() {
  local name="$1" code="$2" expect="$3"
  if [[ "$code" == "$expect" ]]; then
    echo "PASS $name ($code)"
    pass=$((pass+1))
  else
    echo "FAIL $name (got $code expected $expect)"
    fail=$((fail+1))
  fi
}

TOKEN=$(curl -sf -X POST "$BASE/api/auth/token/" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access'])")
AUTH="Authorization: Bearer $TOKEN"

# Module data APIs (overview + 1.1–1.7 dashboards)
for path in \
  "/api/security/soc-kpis/?period=24h" \
  "/api/security/threat-feed/?hours=24&limit=10" \
  "/api/security/attack-vector-trends/?period=24h" \
  "/api/gateways/stats/" \
  "/api/security/rag-pipeline-kpis/?period=24h"; do
  c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE$path")
  check "control $path" "$c" "200"
done

# Inputs / config (firewall-config tab)
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/firewall/config/")
check "firewall config" "$c" "200"
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/vector-providers/")
check "vector providers" "$c" "200"
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/mcp-connector/servers/")
check "mcp servers" "$c" "200"

# Policy (1.2)
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/policies/?policy_domain=global")
check "policies list" "$c" "200"

# Kill-switch / model isolation (1.6)
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/kill-switches/")
check "kill switches" "$c" "200"
c=$(curl -s -o /dev/null -w "%{http_code}" -H "$AUTH" "$BASE/api/models/status/")
check "model status" "$c" "200"

# Firewall gateway public URL (replaces /api/agents/gateway-url/)
c=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/gateways/public-url/")
check "gateway public-url" "$c" "200"

# Gateway health + simulator route exists
c=$(curl -s -o /dev/null -w "%{http_code}" "$GW/health")
if [[ "$c" == "200" || "$c" == "503" ]]; then
  check "gateway health (up or degraded)" "$c" "$c"
else
  check "gateway health" "$c" "200"
fi
c=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$GW/v1/chat/completions" -H 'Content-Type: application/json' -d '{"model":"auto","messages":[{"role":"user","content":"hello"}],"max_tokens":1}' 2>/dev/null || echo "000")
if [[ "$c" == "200" || "$c" == "401" || "$c" == "403" || "$c" == "422" ]]; then
  check "chat completions endpoint reachable" "$c" "$c"
  pass=$((pass+1))
else
  echo "FAIL chat completions endpoint (got $c)"
  fail=$((fail+1))
fi

echo "---"
echo "passed=$pass failed=$fail"
exit $([[ $fail -eq 0 ]] && echo 0 || echo 1)
