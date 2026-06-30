#!/usr/bin/env bash
# Run one MCP frontend adversarial regression iteration (5–20).
# Logs to scripts/ralph/regression.log and marks the matching R-story passes:true on success.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="$SCRIPT_DIR/regression.log"
PRD="$SCRIPT_DIR/prd-mcp-frontend-adversarial.json"

LOCK_DIR="$SCRIPT_DIR/.regression.lockdir"
REGRESSION_LOCK_HELD_BY_BATCH=0
batch_pid="${REGRESSION_BATCH_PID:-}"
if [[ -n "$batch_pid" ]] && [[ -f "$LOCK_DIR/owner.pid" ]] && [[ "$(<"$LOCK_DIR/owner.pid")" == "$batch_pid" ]]; then
  REGRESSION_LOCK_HELD_BY_BATCH=1
elif mkdir "$LOCK_DIR" 2>/dev/null; then
  echo $$ >"$LOCK_DIR/owner.pid"
else
  stale_owner=""
  if [[ -f "$LOCK_DIR/owner.pid" ]]; then
    stale_owner="$(<"$LOCK_DIR/owner.pid")"
  fi
  if [[ -n "$stale_owner" ]] && kill -0 "$stale_owner" 2>/dev/null; then
    echo "Another MCP regression iteration is already running (lock: $LOCK_DIR owner PID $stale_owner)" >&2
    exit 1
  fi
  rm -rf "$LOCK_DIR"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another MCP regression iteration is already running (lock: $LOCK_DIR)" >&2
    exit 1
  fi
  echo $$ >"$LOCK_DIR/owner.pid"
fi
cleanup_regression_lock() {
  if (( REGRESSION_LOCK_HELD_BY_BATCH )); then
    return 0
  fi
  if [[ -d "$LOCK_DIR" ]] && [[ -f "$LOCK_DIR/owner.pid" ]] && [[ "$(<"$LOCK_DIR/owner.pid")" == "$$" ]]; then
    rm -rf "$LOCK_DIR"
  fi
}
trap cleanup_regression_lock EXIT INT TERM
BROKER_URL="${MCP_BROKER_URL:-http://127.0.0.1:8311}"
ITER="${1:?Usage: run_regression_iteration.sh <iteration_number 5-24>}"

if (( ITER < 5 || ITER > 24 )); then
  echo "iteration must be 5–24 (got $ITER)" >&2
  exit 1
fi

R_NUM=$((ITER - 4))
# R1–R16 map to regression iters 5–20; iters 21–24 are extra validation (no PRD story).
R_ID=""
if (( R_NUM >= 1 && R_NUM <= 16 )); then
  R_ID="R${R_NUM}-regression-iter${ITER}"
fi

log() {
  echo "$*" | tee -a "$LOG"
}

run_gate() {
  local name="$1" rc=0
  shift
  log "--- $name ---"
  (cd "$REPO_ROOT" && "$@") >>"$LOG" 2>&1 || rc=$?
  if (( rc == 0 )); then
    log "PASS: $name"
    return 0
  fi
  log "FAIL: $name (exit $rc)"
  return 1
}

run_gate_with_retry() {
  local name="$1" attempts="${2:-2}" rc=1
  shift 2
  local i
  for ((i = 1; i <= attempts; i++)); do
    if run_gate "${name} (attempt ${i}/${attempts})" "$@"; then
      return 0
    fi
    if (( i < attempts )); then
      log "Retrying ${name} after 20s cooldown..."
      sleep 20
    fi
  done
  return 1
}

ADVERSARIAL_TEST_BROKER="mcp-broker-adversarial-test"

_rm_adversarial_test_broker() {
  local attempt
  for attempt in $(seq 1 10); do
    docker rm -f "$ADVERSARIAL_TEST_BROKER" >>"$LOG" 2>&1 || true
    if ! docker ps -aq --filter "name=^/${ADVERSARIAL_TEST_BROKER}$" 2>/dev/null | grep -q .; then
      return 0
    fi
    sleep 2
  done
  log "WARN: ${ADVERSARIAL_TEST_BROKER} still present after cleanup retries"
  return 1
}

prep_for_pytest() {
  log "--- prep: docker cooldown before isolated pytest broker ---"
  _rm_adversarial_test_broker || true
  for org in adv-org-alpha adv-org-beta; do
    local ids
    ids="$(docker ps -aq --filter "label=ai_mesh.org_slug=${org}" 2>/dev/null || true)"
    if [[ -n "${ids}" ]]; then
      docker rm -f ${ids} >>"$LOG" 2>&1 || true
    fi
  done
  sleep 25
  if curl -sf "${BROKER_URL%/}/health" >/dev/null 2>&1; then
    log "live broker healthy at ${BROKER_URL}"
  else
    log "WARN: live broker not reachable at ${BROKER_URL} (pytest uses isolated broker)"
  fi
}

prep_for_frontend() {
  log "--- prep: warm live sandboxes before frontend e2e ---"
  local key="${MCP_BROKER_INTERNAL_KEY:-dev-mcp-broker-key-change-me}"
  for org in adv-org-alpha adv-org-beta; do
    curl -sf -X POST "${BROKER_URL%/}/v1/sandbox/${org}/ensure" \
      -H "Content-Type: application/json" \
      -H "X-MCP-Broker-Key: ${key}" \
      -d '{"warm":true}' >>"$LOG" 2>&1 || true
  done
  sleep 10
}

log ""
LABEL="regression iter $ITER"
if [[ -n "$R_ID" ]]; then
  LABEL="$LABEL ($R_ID)"
else
  LABEL="$LABEL (extra validation, no PRD mark)"
fi
log "=== Regression iteration $ITER — $LABEL $(date -u +%FT%TZ) ==="

FAILED=0

run_gate_with_retry "parallel org agents --full" 2 \
  node scripts/ralph/run_parallel_org_agents.mjs --full || FAILED=1

prep_for_pytest

run_gate_with_retry "adversarial pytest full" 2 bash -c \
  'cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_sandbox_adversarial.py -q' \
  || FAILED=1

prep_for_frontend

run_gate_with_retry "frontend_parallel_orgs.mjs" 2 bash -c \
  'BASE_URL=http://127.0.0.1:8180 node tests/e2e/mcp_sandbox_adversarial/frontend_parallel_orgs.mjs' \
  || FAILED=1

if (( ITER == 20 )); then
  run_gate "frontend lint+build" bash -c 'cd frontend && npm run lint && npm run build' || FAILED=1
fi

if (( FAILED != 0 )); then
  log "=== Regression iteration $ITER FAILED ==="
  exit 1
fi

# Mark R-story passes:true in PRD (R1–R16 only)
if [[ -n "$R_ID" ]]; then
  if command -v jq >/dev/null 2>&1; then
    tmp="$(mktemp)"
    jq --arg id "$R_ID" '(.userStories[] | select(.id == $id) | .passes) = true' "$PRD" >"$tmp"
    mv "$tmp" "$PRD"
    log "Marked $R_ID passes:true in prd-mcp-frontend-adversarial.json"
  else
    log "WARN: jq not found — update $R_ID passes:true manually"
  fi
else
  log "Extra validation iter $ITER — no PRD story to mark"
fi

log "=== Regression iteration $ITER OK ==="
exit 0
