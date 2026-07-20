#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIDFILE="$SCRIPT_DIR/regression_batch.pid"
LOG="$SCRIPT_DIR/regression_batch.log"
LOCK_DIR="$SCRIPT_DIR/.regression.lockdir"

if [[ -f "$PIDFILE" ]]; then
  old_pid="$(<"$PIDFILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "Regression batch already running (PID $old_pid)" >&2
    exit 1
  fi
fi

echo $$ >"$PIDFILE"
exec >>"$LOG" 2>&1

cleanup_solo_batch() {
  if [[ -f "$PIDFILE" ]] && [[ "$(<"$PIDFILE")" == "$$" ]]; then
    rm -f "$PIDFILE"
  fi
  if [[ -d "$LOCK_DIR" ]] && [[ -f "$LOCK_DIR/owner.pid" ]] && [[ "$(<"$LOCK_DIR/owner.pid")" == "$$" ]]; then
    rm -rf "$LOCK_DIR"
  fi
}
trap cleanup_solo_batch EXIT INT TERM

acquire_batch_lock() {
  while true; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      echo $$ >"$LOCK_DIR/owner.pid"
      return 0
    fi
    local opid=""
    if [[ -f "$LOCK_DIR/owner.pid" ]]; then
      opid="$(<"$LOCK_DIR/owner.pid")"
    fi
    if [[ -z "$opid" ]] || ! kill -0 "$opid" 2>/dev/null; then
      echo "Removing stale regression lock (owner=${opid:-unknown}) $(date -u +%FT%TZ)"
      rm -rf "$LOCK_DIR"
      continue
    fi
    echo "Another MCP regression batch holds lock (PID $opid)" >&2
    exit 1
  done
}

echo "=== SOLO_BATCH_11_20 start PID=$$ $(date -u +%FT%TZ) ==="
acquire_batch_lock
export REGRESSION_BATCH_PID=$$
cd "$REPO_ROOT"

for i in 11 12 13 14 15 16 17 18 19 20; do
  echo "BATCH_ITER=$i $(date -u +%FT%TZ)"
  if ! ./scripts/ralph/run_regression_iteration.sh "$i"; then
    rc=$?
    echo "SOLO_BATCH_11_20 FAILED at iteration=$i exit=$rc $(date -u +%FT%TZ)"
    exit 1
  fi
done

echo "=== SOLO_BATCH_11_20 complete $(date -u +%FT%TZ) ==="
