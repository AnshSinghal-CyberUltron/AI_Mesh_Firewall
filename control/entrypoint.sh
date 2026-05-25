#!/bin/sh
set -e

if echo "$*" | grep -qE "daphne|gunicorn|uvicorn"; then
  /app/.venv/bin/python manage.py migrate --noinput
  /app/.venv/bin/python manage.py collectstatic --noinput
  /app/.venv/bin/python manage.py register_embedded_agents
  /app/.venv/bin/python manage.py assign_superuser_org --org-slug default --org-name "Default Organization" 2>/dev/null || true
fi

exec "$@"
