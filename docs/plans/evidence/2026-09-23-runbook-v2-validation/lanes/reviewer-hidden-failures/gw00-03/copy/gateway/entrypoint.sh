#!/bin/sh
# Gateway entrypoint — derive gunicorn worker count from the cgroup-aware detector
# (shared/ai_mesh_shared/resource_budget.py) so the gateway uses the full machine
# it is given: ~one async UvicornWorker per core, RAM-bounded with headroom.
#
# An explicit WEB_CONCURRENCY (env) still wins, but GW03 requires it to be logged
# as a named deviation with the detector's value beside it. A detector failure
# falls back to FALLBACK_WORKERS and is also logged as a deviation (v1 must boot).
#
# GATEWAY_ENTRYPOINT_DRYRUN=1 prints the resolved command and exits 0 (used by the
# perf proof to show worker count without needing DB/Redis).  See docs/perf/.
set -eu

FALLBACK_WORKERS=4

DETECTED="$(python -m ai_mesh_shared.resource_budget --value workers 2>/dev/null || true)"

if [ -n "${WEB_CONCURRENCY:-}" ]; then
    WORKERS="$WEB_CONCURRENCY"
    WSRC="env-override"
    echo "[gateway-entrypoint] WEB_CONCURRENCY override=${WORKERS} detected=${DETECTED:-unreadable} (deviation)" >&2
elif [ -n "$DETECTED" ]; then
    WORKERS="$DETECTED"
    WSRC="detector"
else
    WORKERS="$FALLBACK_WORKERS"
    WSRC="fallback"
    echo "[gateway-entrypoint] FALLBACK_WORKERS=${WORKERS} detected=unreadable (deviation)" >&2
fi

# One diagnostic line + the full budget, so the running config is always in the logs.
# Normalize the env for gunicorn: gunicorn itself reads WEB_CONCURRENCY at
# config-import time (`int(os.environ.get("WEB_CONCURRENCY", 1))`) and crashes on
# an empty string. Export the resolved integer so gunicorn's own default always
# matches our --workers and can never be "" — regardless of how the env arrived.
export WEB_CONCURRENCY="$WORKERS"

# Size the per-worker offload pools from the detector unless the operator pinned
# them (scanner=CPU Tier-1, bedrock=network Tier-2 from nofile/workers, vault=
# Postgres conn pool). Scanner stays CPU-clamped; bedrock is FD-budget sized.
_set_default() {  # _set_default VAR field
    eval "_cur=\${$1:-}"
    if [ -z "$_cur" ]; then
        _val="$(python -m ai_mesh_shared.resource_budget --value "$2" 2>/dev/null || true)"
        [ -n "$_val" ] && export "$1=$_val"
    fi
}
_set_default GATEWAY_SCANNER_THREAD_POOL_SIZE scanner_pool
_set_default GATEWAY_SCAN_THREAD_POOL_SIZE scanner_pool
_set_default GATEWAY_BEDROCK_THREAD_POOL_SIZE bedrock_pool
_set_default GATEWAY_VAULT_POOL_MAX vault_pool
# Per-worker Redis pool ceiling for the middleware rate-limit pool (was fixed 300).
_set_default GATEWAY_REDIS_MAX_CONNECTIONS redis_pool

echo "[gateway-entrypoint] WEB_CONCURRENCY=$WORKERS (source=$WSRC)" >&2
python -m ai_mesh_shared.resource_budget --json 2>/dev/null \
    | sed 's/^/[gateway-entrypoint]   /' >&2 || true

if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    echo "[gateway-entrypoint] PROMETHEUS_MULTIPROC_DIR=$PROMETHEUS_MULTIPROC_DIR" >&2
fi

set -- gunicorn ai_mesh_gateway.main:app \
    -k uvicorn.workers.UvicornWorker \
    --config /app/gateway/gunicorn.conf.py \
    --bind 0.0.0.0:8300 \
    --workers "$WORKERS" \
    --worker-connections "${GUNICORN_WORKER_CONNECTIONS:-20000}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-60}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-65}"

if [ "${GATEWAY_ENTRYPOINT_DRYRUN:-0}" = "1" ]; then
    echo "[gateway-entrypoint] DRYRUN would exec: $*" >&2
    exit 0
fi

exec "$@"
