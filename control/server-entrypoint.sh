#!/bin/sh
# Control-plane server entrypoint — replace the single Daphne process (dev) / the
# hardcoded `--workers 5` (prod) with gunicorn managing N UvicornWorker processes,
# N derived from the cgroup-aware detector (shared/ai_mesh_shared/resource_budget.py)
# so the control plane uses every core instead of one.
#   Baseline: 1 daphne = 346 rps @ 1.16 cores (docs/perf/BASELINE.md).
#
# Invocation mirrors the proven prod command (docker-compose.prod.yml): stock
# `uvicorn.workers.UvicornWorker` + `--forwarded-allow-ips *` (Channels websockets
# work; cross-process group sends go through the Redis channel layer). The ONLY
# change is that --workers is dynamic. Migrations are NOT run here — control's CMD
# never did (they run out-of-band), and this entrypoint preserves that exactly.
#
# CONTROL_WEB_CONCURRENCY (env) ALWAYS overrides the detector. If the detector
# fails, fall back to a safe static count so control can never fail to boot.
# CONTROL_ENTRYPOINT_DRYRUN=1 prints the resolved command and exits 0 (perf proof).
set -eu

FALLBACK_WORKERS=2

if [ -n "${CONTROL_WEB_CONCURRENCY:-}" ]; then
    WORKERS="$CONTROL_WEB_CONCURRENCY"
    WSRC="env-override"
elif WORKERS="$(python -m ai_mesh_shared.resource_budget --value workers 2>/dev/null)" \
        && [ -n "$WORKERS" ]; then
    WSRC="detector"
else
    WORKERS="$FALLBACK_WORKERS"
    WSRC="fallback"
fi

# gunicorn reads WEB_CONCURRENCY at config-import time and crashes on an empty
# string; control inherits the shared .env which may carry WEB_CONCURRENCY. Export
# the resolved integer so gunicorn's own default matches --workers and is never "".
export WEB_CONCURRENCY="$WORKERS"

echo "[control-entrypoint] CONTROL workers=$WORKERS (source=$WSRC)" >&2
python -m ai_mesh_shared.resource_budget --json 2>/dev/null \
    | sed 's/^/[control-entrypoint]   /' >&2 || true

set -- gunicorn main_app.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers "$WORKERS" \
    --worker-connections "${GUNICORN_WORKER_CONNECTIONS:-1000}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-65}" \
    --forwarded-allow-ips "${GUNICORN_FORWARDED_ALLOW_IPS:-*}"

if [ "${CONTROL_ENTRYPOINT_DRYRUN:-0}" = "1" ]; then
    echo "[control-entrypoint] DRYRUN would exec: $*" >&2
    exit 0
fi

exec "$@"
