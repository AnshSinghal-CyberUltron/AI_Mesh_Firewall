#!/usr/bin/env bash
# Live structural multi-tenant isolation probe (Critical gate). Complements the
# pytest suite ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py with fast,
# dependency-free docker-level assertions:
#   1. two distinct orgs -> two distinct sandbox containers
#   2. each sandbox on its OWN per-org network (no shared sandbox network)
#   3. raw TCP cross-reach A -> B's agent port is UNREACHABLE (network isolation)
#   4. a filesystem marker written in A's data dir is NOT visible in B
# Any leak is a CRITICAL failure.
#
# Usage: bash tests/e2e/mcp_sandbox_adversarial/sandbox_isolation_probe.sh
# Env: MCP_BROKER_URL (default :8311), MCP_BROKER_INTERNAL_KEY (or read from broker container)
set -u

BROKER_URL="${MCP_BROKER_URL:-http://localhost:8311}"
BROKER_CONTAINER="${BROKER_CONTAINER:-ai_mesh_mcp_broker}"
BKEY="${MCP_BROKER_INTERNAL_KEY:-$(docker exec "$BROKER_CONTAINER" sh -c 'echo -n "$MCP_BROKER_INTERNAL_KEY"' 2>/dev/null)}"
A="isoprobe-a"; B="isoprobe-b"
AGENT_PORT="${MCP_SANDBOX_AGENT_PORT:-8790}"

fail() { echo "FAIL sandbox_isolation_probe (CRITICAL): $1"; cleanup; exit 1; }
ensure() { curl -s -m 60 -X POST "$BROKER_URL/v1/sandbox/$1/ensure" -H "X-MCP-Broker-Key: $BKEY" -H 'Content-Type: application/json' -d '{"warm":true}'; }
cleanup() {
  for o in "$A" "$B"; do curl -s -m 30 -X DELETE "$BROKER_URL/v1/sandbox/$o" -H "X-MCP-Broker-Key: $BKEY" -o /dev/null 2>/dev/null; done
}

[ -n "$BKEY" ] || fail "no broker key"

echo "[1/4] ensure two distinct org sandboxes"
ensure "$A" | grep -q '"status":"running"' || fail "ensure $A did not report running"
ensure "$B" | grep -q '"status":"running"' || fail "ensure $B did not report running"
sleep 2
docker ps --format '{{.Names}}' | grep -q "$A-mcp-sandbox" || fail "$A container missing"
docker ps --format '{{.Names}}' | grep -q "$B-mcp-sandbox" || fail "$B container missing"

echo "[2/4] each sandbox on its OWN per-org network only"
NETA=$(docker inspect "$A-mcp-sandbox" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | tr -s ' ')
NETB=$(docker inspect "$B-mcp-sandbox" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | tr -s ' ')
echo "  A nets: $NETA | B nets: $NETB"
echo "$NETA" | grep -q "mcp_sandbox_net_$A" || fail "$A not on its per-org net"
echo "$NETB" | grep -q "mcp_sandbox_net_$B" || fail "$B not on its per-org net"
echo "$NETA" | grep -q "mcp_sandbox_net_$B" && fail "$A is ALSO on $B's net (leak!)"
echo "$NETB" | grep -q "mcp_sandbox_net_$A" && fail "$B is ALSO on $A's net (leak!)"

echo "[3/4] raw TCP cross-reach A -> B agent port must be UNREACHABLE"
BIP=$(docker inspect "$B-mcp-sandbox" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}} {{end}}' | awk '{print $1}')
[ -n "$BIP" ] || fail "could not resolve B sandbox IP"
REACH=$(docker exec "$A-mcp-sandbox" sh -c "timeout 4 sh -c 'echo > /dev/tcp/$BIP/$AGENT_PORT' 2>&1 && echo REACHABLE || echo UNREACHABLE" 2>&1 | tail -1)
[ "$REACH" = "UNREACHABLE" ] || fail "A can reach B's agent at $BIP:$AGENT_PORT (network isolation breach)"

echo "[4/4] filesystem marker written in A is NOT visible in B"
MARK="ORG_A_SECRET_$(docker exec "$A-mcp-sandbox" sh -c 'echo $$')_marker"
docker exec "$A-mcp-sandbox" sh -c "mkdir -p /data/mcp-auth && echo '$MARK' > /data/mcp-auth/secret_a.txt" 2>/dev/null || fail "could not write marker in A"
BREAD=$(docker exec "$B-mcp-sandbox" sh -c 'cat /data/mcp-auth/secret_a.txt 2>/dev/null || echo __ABSENT__' 2>&1 | tail -1)
[ "$BREAD" = "__ABSENT__" ] || fail "B can read A's marker (filesystem leak): $BREAD"

cleanup
echo "PASS sandbox_isolation_probe: distinct containers + per-org networks, cross-reach blocked, no fs marker leak"
