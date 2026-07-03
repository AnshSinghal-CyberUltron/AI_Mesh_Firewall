#!/bin/sh
# Gateway entrypoint — derive gunicorn worker count from the cgroup-aware detector
# (shared/ai_mesh_shared/resource_budget.py) so the gateway uses the full machine
# it is given: ~one async UvicornWorker per core, RAM-bounded with headroom.
#
# An explicit WEB_CONCURRENCY (env) ALWAYS wins — operators pin it when they want a
# fixed count; the detector only fills the default (replacing the old hardcoded 4).
# If the detector ever fails to import/run, we fall back to a safe static default so
# the gateway can never fail to boot over a sizing calculation.
#
# GATEWAY_ENTRYPOINT_DRYRUN=1 prints the resolved command and exits 0 (used by the
# perf proof to show worker count without needing DB/Redis).  See docs/perf/.
set -eu

FALLBACK_WORKERS=4

if [ -n "${WEB_CONCURRENCY:-}" ]; then
    WORKERS="$WEB_CONCURRENCY"
    WSRC="env-override"
elif WORKERS="$(python -m ai_mesh_shared.resource_budget --value workers 2>/dev/null)" \
        && [ -n "$WORKERS" ]; then
    WSRC="detector"
else
    WORKERS="$FALLBACK_WORKERS"
    WSRC="fallback"
fi

# One diagnostic line + the full budget, so the running config is always in the logs.
# Normalize the env for gunicorn: gunicorn itself reads WEB_CONCURRENCY at
# config-import time (`int(os.environ.get("WEB_CONCURRENCY", 1))`) and crashes on
# an empty string. Export the resolved integer so gunicorn's own default always
# matches our --workers and can never be "" — regardless of how the env arrived.
export WEB_CONCURRENCY="$WORKERS"

echo "[gateway-entrypoint] WEB_CONCURRENCY=$WORKERS (source=$WSRC)" >&2
python -m ai_mesh_shared.resource_budget --json 2>/dev/null \
    | sed 's/^/[gateway-entrypoint]   /' >&2 || true

set -- gunicorn ai_mesh_gateway.main:app \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8300 \
    --workers "$WORKERS" \
    --worker-connections "${GUNICORN_WORKER_CONNECTIONS:-1000}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-60}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-65}"

if [ "${GATEWAY_ENTRYPOINT_DRYRUN:-0}" = "1" ]; then
    echo "[gateway-entrypoint] DRYRUN would exec: $*" >&2
    exit 0
fi

exec "$@"
