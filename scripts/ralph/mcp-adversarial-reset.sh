#!/usr/bin/env bash
# Full reset for MCP frontend adversarial Ralph regression loop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRD="$SCRIPT_DIR/prd-mcp-frontend-adversarial.json"
PROGRESS="$SCRIPT_DIR/progress.txt"
DATE="$(date +%Y-%m-%d)"

echo "=== MCP adversarial FULL RESET $DATE ==="

pkill -9 -f 'run_regression|run_iters|ralph-mcp-frontend|regression_batch' 2>/dev/null || true
rm -rf "$SCRIPT_DIR/.regression.lockdir" "$SCRIPT_DIR/regression_batch.pid"

if [[ -f "$SCRIPT_DIR/regression.log" ]]; then
  mv "$SCRIPT_DIR/regression.log" "$SCRIPT_DIR/regression.log.bak.$(date +%s)"
fi
touch "$SCRIPT_DIR/regression.log"

if command -v jq >/dev/null 2>&1; then
  tmp="$(mktemp)"
  jq '(.userStories[] | select(.id | startswith("R")) | .passes) = false' "$PRD" >"$tmp"
  mv "$tmp" "$PRD"
  echo "PRD: R1–R16 reset to passes:false (F stories unchanged)"
else
  echo "WARN: jq not found — reset R stories in PRD manually" >&2
fi

echo "" >>"$PROGRESS"
echo "## MCP Frontend Adversarial Ralph Loop" >>"$PROGRESS"
echo "- ${DATE} FULL RESET — user requested restart from logical iter 1; killed regression batches; R1–R16 passes:false." >>"$PROGRESS"

echo "Reset complete. Run: ./scripts/ralph/run_logical_iteration.sh 1"
echo "Or full loop: see scripts/ralph/START_IN_TERMINAL.md"
