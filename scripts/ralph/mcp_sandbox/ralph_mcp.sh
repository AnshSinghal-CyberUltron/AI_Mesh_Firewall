#!/usr/bin/env bash
# Ralph loop — MCP sandbox isolation (dedicated broker :8312, orgs epsilon/zeta).
# Host-aware: 8GB Docker VM shared with a parallel session's loop. Each iteration waits for
# host headroom (load < LOAD_MAX) and keeps broker#2 alive, so it gives REAL isolation signal
# instead of host-saturation false failures.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOG="$SCRIPT_DIR/ralph_mcp.log"
MAX_ITERS="${1:-30}"
PROMISE="${2:-COMPLETE and tested from frontend and backend}"
MIN_ITERS="${3:-20}"
LOAD_MAX="${LOAD_MAX:-16}"                 # wait until 1-min load < this before each iteration
BROKER_NAME="ai_mesh_mcp_broker_2"
BROKER_URL="${MCP_BROKER_URL:-http://127.0.0.1:8312}"
KEY="${MCP_BROKER_INTERNAL_KEY:-dev-mcp-broker-key-change-me}"
BROKER_BASE_NET="${MCP_BROKER_BASE_NET:-mcp_sandbox_bridge_2}"   # broker#2's OWN base bridge
BROKER_IMAGE="${MCP_BROKER_IMAGE:-ai_mesh_firewall-mcp-broker}"
BROKER_HOST_PORT="${MCP_BROKER_HOST_PORT:-8312}"
export MCP_BROKER_URL="$BROKER_URL" MCP_BROKER_INTERNAL_KEY="$KEY"

recreate_broker() {  # under host saturation broker#2 can be REMOVED (not just stopped); `docker start`
                     # cannot bring back a removed container — recreate it from the :8311 template.
                     # NEVER touches :8311 / alpha-delta; own net mcp_sandbox_bridge_2, own port 8312.
  docker network inspect "$BROKER_BASE_NET" >/dev/null 2>&1 || docker network create "$BROKER_BASE_NET" >/dev/null 2>&1 || true
  docker rm -f "$BROKER_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$BROKER_NAME" \
    --network "$BROKER_BASE_NET" \
    -p "${BROKER_HOST_PORT}:8311" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e MCP_BROKER_INTERNAL_KEY="$KEY" \
    -e MCP_SANDBOX_IMAGE=ai-mesh/mcp-sandbox:latest \
    -e MCP_SANDBOX_NETWORK="$BROKER_BASE_NET" \
    -e MCP_SANDBOX_IDLE_TIMEOUT=3600 \
    -e MCP_BROKER_CONTAINER_NAME="$BROKER_NAME" \
    --restart unless-stopped \
    "$BROKER_IMAGE" >/dev/null 2>&1 || true
}

ensure_broker() {  # restart/recreate + wait for /health 200 (broker#2 gets OOM-killed OR removed under load)
  for _ in $(seq 1 45); do
    [ "$(curl -s --max-time 3 -H "X-MCP-Broker-Key: $KEY" -o /dev/null -w '%{http_code}' "$BROKER_URL/health" 2>/dev/null)" = "200" ] && return 0
    if docker inspect "$BROKER_NAME" >/dev/null 2>&1; then
      docker start "$BROKER_NAME" >/dev/null 2>&1 || true   # exists but stopped
    else
      recreate_broker                                        # gone entirely — rebuild it
    fi
    sleep 2
  done
  return 1
}

wait_for_headroom() {  # up to ~50min; returns 1 if never free
  for _ in $(seq 1 100); do
    load=$(uptime | sed -E 's/.*load averages?: ([0-9.]+).*/\1/' | tr -d ' ')
    awk -v l="$load" -v m="$LOAD_MAX" 'BEGIN{exit !(l+0<m+0)}' && { echo "  host load $load < $LOAD_MAX — go" | tee -a "$LOG"; return 0; }
    echo "  host load $load >= $LOAD_MAX — waiting for headroom (parallel session active)…" | tee -a "$LOG"
    sleep 30
  done
  return 1
}

echo "=== Ralph MCP-sandbox start $(date -u +%FT%TZ) max=$MAX_ITERS min=$MIN_ITERS load_max=$LOAD_MAX ===" | tee -a "$LOG"
for ((i=1; i<=MAX_ITERS; i++)); do
  echo "----- iter $i/$MAX_ITERS $(date -u +%FT%TZ) -----" | tee -a "$LOG"
  wait_for_headroom || echo "  (no headroom in window — proceeding anyway)" | tee -a "$LOG"
  ensure_broker || { echo "  broker#2 unrecoverable; sleep 60 + retry iter" | tee -a "$LOG"; sleep 60; ((i--)); continue; }
  OUTPUT=$( (cd "$REPO_ROOT" && claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md") 2>&1 | tee -a "$LOG" | tee /dev/stderr ) || true
  if printf '%s' "$OUTPUT" | grep -q "<promise>${PROMISE}</promise>"; then
    if (( i >= MIN_ITERS )); then echo "=== Ralph MCP-sandbox COMPLETE at iter $i (>= min $MIN_ITERS) $(date -u +%FT%TZ) ===" | tee -a "$LOG"; exit 0; fi
    echo "=== COMPLETE at $i but below min $MIN_ITERS — continuing rigorous re-verification ===" | tee -a "$LOG"
  fi
  echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$SCRIPT_DIR/prd.json" 2>/dev/null) story(ies)" | tee -a "$LOG"
  sleep 2
done
echo "=== Ralph MCP-sandbox hit max_iters ($MAX_ITERS) without COMPLETE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
exit 1
