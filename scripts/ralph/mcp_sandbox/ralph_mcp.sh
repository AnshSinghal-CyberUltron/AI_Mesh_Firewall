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
export MCP_BROKER_URL="$BROKER_URL" MCP_BROKER_INTERNAL_KEY="$KEY"

ensure_broker() {  # restart + wait for /health 200 (broker#2 gets OOM-killed under load)
  for _ in $(seq 1 45); do
    [ "$(curl -s --max-time 3 -H "X-MCP-Broker-Key: $KEY" -o /dev/null -w '%{http_code}' "$BROKER_URL/health" 2>/dev/null)" = "200" ] && return 0
    docker start "$BROKER_NAME" >/dev/null 2>&1 || true
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
