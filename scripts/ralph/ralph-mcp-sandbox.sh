#!/usr/bin/env bash
# Ralph loop for MCP per-tenant Docker sandbox — reads prd-mcp-sandbox.json.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/ralph-mcp-sandbox.log"
PRD="$SCRIPT_DIR/prd-mcp-sandbox.json"
PROMISE="${2:-COMPLETE and tested from frontend and backend}"
MAX_ITERS="${1:-50}"

echo "=== Ralph MCP Sandbox start $(date -u +%FT%TZ) max_iters=$MAX_ITERS promise=\"$PROMISE\" ===" | tee -a "$LOG"
for ((i=1; i<=MAX_ITERS; i++)); do
  echo "----- iteration $i/$MAX_ITERS $(date -u +%FT%TZ) -----" | tee -a "$LOG"

  OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE-mcp-sandbox.md" 2>&1 \
            | tee -a "$LOG" | tee /dev/stderr) || true

  if printf '%s' "$OUTPUT" | grep -qF "<promise>${PROMISE}</promise>"; then
    echo "=== Ralph MCP Sandbox COMPLETE at iteration $i $(date -u +%FT%TZ) ===" | tee -a "$LOG"
    exit 0
  fi

  echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$PRD" 2>/dev/null) story(ies)" | tee -a "$LOG"
  sleep 2
done
echo "=== Ralph MCP Sandbox hit max_iters ($MAX_ITERS) without COMPLETE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
exit 1
