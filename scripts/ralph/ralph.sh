#!/usr/bin/env bash
# Ralph loop for AI Mesh Firewall — fresh Claude Code instance per iteration until COMPLETE.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/ralph.log"
PROMISE="COMPLETE"
MAX_ITERS="${1:-30}"      # default 30 (we have ~17 stories; leave headroom for splits/retries)

echo "=== Ralph start $(date -u +%FT%TZ) max_iters=$MAX_ITERS ===" | tee -a "$LOG"
for ((i=1; i<=MAX_ITERS; i++)); do
  echo "----- iteration $i/$MAX_ITERS $(date -u +%FT%TZ) -----" | tee -a "$LOG"

  # Fresh, headless, non-interactive Claude Code instance. CLAUDE.md is the per-iteration prompt.
  OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md" 2>&1 \
            | tee -a "$LOG" | tee /dev/stderr) || true

  # Stop when the model declares all stories pass.
  if printf '%s' "$OUTPUT" | grep -q "<promise>${PROMISE}</promise>"; then
    echo "=== Ralph COMPLETE at iteration $i $(date -u +%FT%TZ) ===" | tee -a "$LOG"
    exit 0
  fi

  # Safety: if nothing committed AND no story flipped, surface it but keep going (next fresh context).
  echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$SCRIPT_DIR/prd.json" 2>/dev/null) story(ies)" | tee -a "$LOG"
  sleep 2
done
echo "=== Ralph hit max_iters ($MAX_ITERS) without COMPLETE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
exit 1
