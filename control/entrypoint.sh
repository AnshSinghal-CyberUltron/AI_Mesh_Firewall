#!/bin/sh
set -e

cd /app/control

if echo "$*" | grep -qE "daphne|gunicorn|uvicorn"; then
  attempt=1
  max_attempts=30
  while ! python manage.py migrate --noinput; do
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "migrate failed after ${max_attempts} attempts" >&2
      exit 1
    fi
    echo "migrate waiting for database (attempt ${attempt}/${max_attempts})..." >&2
    attempt=$((attempt + 1))
    sleep 2
  done
  python manage.py collectstatic --noinput
  python manage.py register_embedded_agents 2>/dev/null || true
  python manage.py assign_superuser_org --org-slug default --org-name "Default Organization" 2>/dev/null || true
  python manage.py ensure_zeroshield_admin 2>/dev/null || true
fi

exec "$@"
