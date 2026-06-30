#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIDFILE="$SCRIPT_DIR/regression_batch.pid"
LOG="$SCRIPT_DIR/regression_batch.log"

if [[ -f "$PIDFILE" ]]; then
  old_pid="$(<"$PIDFILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "Regression batch already running (PID $old_pid)" >&2
    exit 1
  fi
fi

echo $$ >"$PIDFILE"
exec >>"$LOG" 2>&1

echo "=== SOLO_BATCH_11_20 start PID=$$ $(date -u +%FT%TZ) ==="
cd "$REPO_ROOT"

for i in 11 12 13 14 15 16 17 18 19 20; do
  ./scripts/ralph/run_regression_iteration.sh "$i" || exit 1
done

echo "=== SOLO_BATCH_11_20 complete $(date -u +%FT%TZ) ==="
