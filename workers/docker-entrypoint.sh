#!/bin/sh
set -e

wait_for_broker() {
  host="${RABBITMQ_HOST:-rabbitmq}"
  port="${RABBITMQ_PORT:-5672}"
  max_attempts="${BROKER_WAIT_ATTEMPTS:-60}"
  attempt=0

  echo "[workers] Waiting for AMQP broker at ${host}:${port}..."
  while [ "$attempt" -lt "$max_attempts" ]; do
    if python - <<'PY'
import os, socket, sys
host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
port = int(os.environ.get("RABBITMQ_PORT", "5672"))
try:
    with socket.create_connection((host, port), timeout=3):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
    then
      echo "[workers] Broker is reachable."
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 5
  done

  echo "[workers] Broker not reachable after ${max_attempts} attempts." >&2
  return 1
}

wait_for_broker
exec "$@"
