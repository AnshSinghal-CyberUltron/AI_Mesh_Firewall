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
# After resolve, ALWAYS clamp workers to ceil(cgroup cpu_budget) so an affinity
# fallback (host nproc) cannot exceed the container's CPU quota (prod incident:
# 8-vCPU host + cpus:2 → runaway workers holding idle PG connections).
# CONTROL_ENTRYPOINT_DRYRUN=1 prints the resolved command and exits 0 (perf proof).
set -eu

# Containers ship `python` on PATH; bare hosts may only have `python3`.
if command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
else
    echo "[control-entrypoint] FATAL: neither python nor python3 on PATH" >&2
    exit 1
fi

FALLBACK_WORKERS=2

if [ -n "${CONTROL_WEB_CONCURRENCY:-}" ]; then
    WORKERS="$CONTROL_WEB_CONCURRENCY"
    WSRC="env-override"
elif WORKERS="$("$PYTHON_BIN" -m ai_mesh_shared.resource_budget --value workers 2>/dev/null)" \
        && [ -n "$WORKERS" ]; then
    WSRC="detector"
else
    WORKERS="$FALLBACK_WORKERS"
    WSRC="fallback"
fi

# Clamp to cgroup CPU quota (defense-in-depth even when env override is wrong).
CPU_BUDGET="$("$PYTHON_BIN" -m ai_mesh_shared.resource_budget --value cpu_budget 2>/dev/null || true)"
if [ -n "$CPU_BUDGET" ]; then
    CPU_CAP="$(awk "BEGIN { c = int($CPU_BUDGET + 0.999); if (c < 1) c = 1; print c }")"
    case "$WORKERS" in
        ''|*[!0-9]*) ;;
        *)
            if [ "$WORKERS" -gt "$CPU_CAP" ]; then
                echo "[control-entrypoint] clamping workers $WORKERS → $CPU_CAP (cpu_budget=$CPU_BUDGET)" >&2
                WORKERS="$CPU_CAP"
                WSRC="${WSRC}+cgroup-clamp"
            fi
            ;;
    esac
fi

# gunicorn reads WEB_CONCURRENCY at config-import time and crashes on an empty
# string; control inherits the shared .env which may carry WEB_CONCURRENCY. Export
# the resolved integer so gunicorn's own default matches --workers and is never "".
export WEB_CONCURRENCY="$WORKERS"

# Phase 0a C-4b: pin the unset default to 2 (not the detector's asgi_threads).
# An explicit ASGI_THREADS env still overrides. Never export an empty string.
if [ -z "${ASGI_THREADS:-}" ]; then
    ASGI_THREADS=2
fi
export ASGI_THREADS

# Size the Django cache Redis pool per worker from the detector (was fixed 200)
# unless pinned. Generous, scales with cores, never a bottleneck (P5 item 16).
if [ -z "${DJANGO_CACHE_MAX_CONNECTIONS:-}" ]; then
    DJANGO_CACHE_MAX_CONNECTIONS="$("$PYTHON_BIN" -m ai_mesh_shared.resource_budget --value redis_pool 2>/dev/null || true)"
fi
if [ -n "${DJANGO_CACHE_MAX_CONNECTIONS:-}" ]; then
    export DJANGO_CACHE_MAX_CONNECTIONS
fi

echo "[control-entrypoint] CONTROL workers=$WORKERS (source=$WSRC)" >&2
"$PYTHON_BIN" -m ai_mesh_shared.resource_budget --json 2>/dev/null \
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

# C-3: recycle workers after N requests as leak insurance. This is not an OOM fix —
# recycling runs after a request completes; the OOM kill happens during one. SQL
# pushdown (C-1′) is the OOM fix. GUNICORN_MAX_REQUESTS=0 disables recycling.
if [ "${GUNICORN_MAX_REQUESTS:-2000}" != "0" ]; then
    set -- "$@" \
        --max-requests "${GUNICORN_MAX_REQUESTS:-2000}" \
        --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}"
fi

if [ "${CONTROL_ENTRYPOINT_DRYRUN:-0}" = "1" ]; then
    echo "[control-entrypoint] DRYRUN would exec: $*" >&2
    exit 0
fi

exec "$@"
