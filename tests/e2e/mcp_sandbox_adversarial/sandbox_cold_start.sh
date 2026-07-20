#!/usr/bin/env bash
# Regression (live Docker): a COLD per-tenant sandbox must serve the FIRST sync
# without surfacing "MCP sandbox is temporarily unavailable" (bug #4).
#
# Guards two root causes:
#   1. Network wiring — the gateway must be able to reach the broker
#      (broker on the gateway's compose network). If the broker is detached the
#      gateway gets DNS failure -> 5 retries -> "temporarily unavailable".
#   2. Cold-start agent-readiness race — a freshly (re)started sandbox reports
#      "running" before its in-container HTTP agent binds; the broker
#      _post_agent_rpc must retry with backoff so the FIRST RPC still succeeds.
#
# Usage: bash tests/e2e/mcp_sandbox_adversarial/sandbox_cold_start.sh
# Env: CONTROL_URL (default :8100), GATEWAY_CONTAINER, BROKER_CONTAINER,
#      SANDBOX_CONTAINER, TEST_EMAIL, TEST_PASSWORD, SERVER_NAME (stdio server to sync)
set -u

CONTROL_URL="${CONTROL_URL:-http://localhost:8100}"
GATEWAY_CONTAINER="${GATEWAY_CONTAINER:-aimesh_gate-gateway-1}"
BROKER_CONTAINER="${BROKER_CONTAINER:-ai_mesh_mcp_broker}"
SANDBOX_CONTAINER="${SANDBOX_CONTAINER:-zeroshield-mcp-sandbox}"
EMAIL="${TEST_EMAIL:-admin@zeroshield.io}"
PASS="${TEST_PASSWORD:-Adm1n!Pass#2024}"
SERVER_NAME="${SERVER_NAME:-Playwright}"

fail() { echo "FAIL sandbox_cold_start: $1"; exit 1; }

# --- Precondition: gateway can reach the broker (network wiring, root cause #1)
echo "[1/4] gateway -> broker reachability"
REACH=$(docker exec "$GATEWAY_CONTAINER" sh -c 'python3 -c "import urllib.request;print(urllib.request.urlopen(\"http://mcp-broker:8311/health\",timeout=5).read().decode())" 2>&1' 2>&1)
echo "$REACH" | grep -q '"status":"ok"' || fail "gateway cannot reach broker (network regression): $REACH"

# --- Auth
TOK=$(curl -s -m 10 -X POST "$CONTROL_URL/api/auth/token/" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access",""))')
[ -n "$TOK" ] || fail "login failed"

SID=$(curl -s -m 10 "$CONTROL_URL/api/mcp-connector/servers/" -H "Authorization: Bearer $TOK" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); L=d if isinstance(d,list) else d.get('results',[]); print(next((s['id'] for s in L if s['name']=='$SERVER_NAME' and s['transport']=='stdio'), ''))")
[ -n "$SID" ] || fail "stdio server '$SERVER_NAME' not found (register it first)"

# --- Force cold sandbox (simulate idle-reaped)
echo "[2/4] stopping sandbox to force cold start"
docker stop "$SANDBOX_CONTAINER" >/dev/null 2>&1 || echo "  (sandbox not running / already stopped)"

# --- FIRST sync on a cold sandbox must succeed
echo "[3/4] first sync on COLD sandbox"
RESP=$(curl -s -m 140 -X POST "$CONTROL_URL/api/mcp-connector/servers/$SID/tools/" -H "Authorization: Bearer $TOK")
echo "  resp: $(echo "$RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ("synced","error","connection_status")}))')"
ERRV=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("error") or "")')
CONN=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("connection_status") or "")')
SYN=$(echo "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("synced") or 0)')
[ -z "$ERRV" ] || fail "cold sync returned error: $ERRV"
[ "$CONN" = "connected" ] || fail "cold sync connection_status=$CONN (expected connected)"
[ "$SYN" -ge 1 ] 2>/dev/null || fail "cold sync synced=$SYN (expected >=1)"

# --- Sandbox should now be running (proves execution happened in the sandbox)
echo "[4/4] sandbox running after cold sync"
docker ps --format '{{.Names}} {{.Status}}' | grep -q "$SANDBOX_CONTAINER" || fail "sandbox not running after sync"

echo "PASS sandbox_cold_start: cold sandbox served first sync (synced=$SYN, connected, no dead card)"
