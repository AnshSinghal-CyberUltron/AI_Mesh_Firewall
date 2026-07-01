#!/usr/bin/env bash
# Ralph loop for AI Mesh Firewall — fresh Claude Code instance per iteration until COMPLETE.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$SCRIPT_DIR/ralph.log"
PROMISE="${2:-COMPLETE}"
MAX_ITERS="${1:-30}"      # default 30 (we have ~17 stories; leave headroom for splits/retries)
MIN_ITERS="${3:-20}"     # floor: ignore an early COMPLETE and keep running RIGOROUS re-verification
                         # until at least this many iterations have run (don't trust quick all-pass)

echo "=== Ralph start $(date -u +%FT%TZ) max_iters=$MAX_ITERS ===" | tee -a "$LOG"
for ((i=1; i<=MAX_ITERS; i++)); do
  echo "----- iteration $i/$MAX_ITERS $(date -u +%FT%TZ) -----" | tee -a "$LOG"

  # Fresh, headless, non-interactive Claude Code instance. CLAUDE.md is the per-iteration prompt.
  OUTPUT=$(claude --dangerously-skip-permissions --print < "$SCRIPT_DIR/CLAUDE.md" 2>&1 \
            | tee -a "$LOG" | tee /dev/stderr) || true

  # Stop when the model declares all stories pass — but ONLY after the min-iteration floor, so the
  # SDK + leak stories get adversarially re-verified instead of trusting an early all-pass flip.
  if printf '%s' "$OUTPUT" | grep -q "<promise>${PROMISE}</promise>"; then
    if (( i >= MIN_ITERS )); then
      echo "=== Ralph COMPLETE at iteration $i (>= min $MIN_ITERS) $(date -u +%FT%TZ) ===" | tee -a "$LOG"
      exit 0
    fi
    echo "=== COMPLETE detected at $i but below min $MIN_ITERS — continuing RIGOROUS re-verification ===" | tee -a "$LOG"
  fi

  # Safety: if nothing committed AND no story flipped, surface it but keep going (next fresh context).
  echo "remaining: $(jq -r '[.userStories[]|select(.passes==false)]|length' "$SCRIPT_DIR/prd.json" 2>/dev/null) story(ies)" | tee -a "$LOG"
  sleep 2
done
echo "=== Ralph hit max_iters ($MAX_ITERS) without COMPLETE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
exit 1
