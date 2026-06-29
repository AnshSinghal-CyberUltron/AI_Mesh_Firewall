#!/usr/bin/env bash
# Ralph loop — MCP frontend adversarial sandbox isolation (min 20 iterations, max 50).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/ralph-mcp-frontend-adversarial.log"
PRD="$SCRIPT_DIR/prd-mcp-frontend-adversarial.json"
PROMPT="$SCRIPT_DIR/CLAUDE-mcp-frontend-adversarial.md"
PROMISE="${2:-MCP FRONTEND ADVERSARIAL COMPLETE}"
MAX_ITERS="${1:-50}"

echo "=== Ralph MCP Frontend Adversarial start $(date -u +%FT%TZ) max_iters=$MAX_ITERS promise=\"$PROMISE\" ===" | tee -a "$LOG"
for ((i=1; i<=MAX_ITERS; i++)); do
  echo "----- iteration $i/$MAX_ITERS $(date -u +%FT%TZ) -----" | tee -a "$LOG"

  OUTPUT=$(claude --dangerously-skip-permissions --print < "$PROMPT" 2>&1 \
            | tee -a "$LOG" | tee /dev/stderr) || true

  if printf '%s' "$OUTPUT" | grep -qF "<promise>${PROMISE}</promise>"; then
    echo "=== Ralph MCP Frontend Adversarial COMPLETE at iteration $i $(date -u +%FT%TZ) ===" | tee -a "$LOG"
    exit 0
  fi

  echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$PRD" 2>/dev/null) story(ies)" | tee -a "$LOG"
  sleep 2
done
echo "=== Ralph MCP Frontend Adversarial hit max_iters ($MAX_ITERS) without COMPLETE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
exit 1
