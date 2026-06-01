#!/usr/bin/env bash
# Start the full local dev stack including Celery workers (--profile workers).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker compose --profile workers up -d \
  postgres redis rabbitmq control gateway frontend workers workers-beat

echo "Waiting for control health..."
curl --retry 20 --retry-delay 2 --retry-connrefused -sf http://localhost:8100/api/health/ >/dev/null
echo "Waiting for gateway..."
curl --retry 20 --retry-delay 2 --retry-connrefused -sf http://localhost:8300/health >/dev/null

echo "Dev stack is up (workers profile enabled)."
