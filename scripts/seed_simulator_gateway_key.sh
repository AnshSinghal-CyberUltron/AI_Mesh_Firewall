#!/usr/bin/env bash
# Seed Redis simulator:default_gateway_key for UI auto-bootstrap (dev only).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T control python manage.py shell -c "
from core.simulator_seed import ensure_simulator_default_gateway_key
ok = ensure_simulator_default_gateway_key()
print('Simulator default gateway key seed:', 'ok' if ok else 'skipped')
"
