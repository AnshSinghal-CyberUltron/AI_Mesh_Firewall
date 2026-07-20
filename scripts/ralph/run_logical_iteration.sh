#!/usr/bin/env bash
# Map scratchpad logical iteration (1–20) to regression gate.
# Logical 1–16 → regression iters 5–20 → R1–R16 (PRD mark on success).
# Logical 17–20 → regression iters 21–24 → full gate only (no PRD story).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRATCHPAD="$REPO_ROOT/.cursor/ralph/scratchpad.md"

LOGICAL="${1:?Usage: run_logical_iteration.sh <logical_iteration 1-20>}"

if (( LOGICAL < 1 || LOGICAL > 20 )); then
  echo "logical iteration must be 1–20 (got $LOGICAL)" >&2
  exit 1
fi

REGRESSION_ITER=$((LOGICAL + 4))

echo "=== Logical iteration $LOGICAL → regression iter $REGRESSION_ITER ==="

cd "$REPO_ROOT"
if ! ./scripts/ralph/run_regression_iteration.sh "$REGRESSION_ITER"; then
  echo "Logical iteration $LOGICAL FAILED (regression iter $REGRESSION_ITER)" >&2
  exit 1
fi

# Bump scratchpad iteration counter on success
NEXT=$((LOGICAL + 1))
if [[ -f "$SCRATCHPAD" ]]; then
  if command -v perl >/dev/null 2>&1; then
    perl -i -pe "s/^iteration: \\d+/iteration: $NEXT/" "$SCRATCHPAD"
  else
    sed -i.bak "s/^iteration: [0-9]*/iteration: $NEXT/" "$SCRATCHPAD" && rm -f "${SCRATCHPAD}.bak"
  fi
fi

if (( LOGICAL == 16 )); then
  echo "R1–R16 all marked in PRD when regression iter 20 completes."
elif (( LOGICAL > 16 )); then
  echo "Extra validation iteration $LOGICAL complete (no PRD story)."
fi

echo "Logical iteration $LOGICAL OK — scratchpad now iteration $NEXT"
exit 0
