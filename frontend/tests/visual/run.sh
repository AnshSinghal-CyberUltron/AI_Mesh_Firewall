#!/usr/bin/env bash
# Run the visual+behavior regression gate against the running dev stack.
#
# Mints a short-lived JWT from the control-plane container, then runs audit.mjs.
# Requires: the docker compose stack up (frontend on :8180, control-1 healthy) and
# a resolvable playwright (see README.md).
#
# Usage:
#   tests/visual/run.sh                 # all surfaces, both themes, 4 widths
#   VISUAL_ONLY=module-1-3,overview tests/visual/run.sh
set -euo pipefail

CONTROL_CTR="${CONTROL_CTR:-ai_mesh_firewall-control-1}"
export VISUAL_BASE_URL="${VISUAL_BASE_URL:-http://127.0.0.1:8180}"

if [ -z "${VISUAL_TOKEN:-}" ]; then
  echo "Minting JWT from ${CONTROL_CTR}..."
  VISUAL_TOKEN="$(docker exec "${CONTROL_CTR}" python manage.py shell -c "
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
U=get_user_model()
u=U.objects.filter(is_superuser=True).first() or U.objects.first()
print(RefreshToken.for_user(u).access_token)" 2>/dev/null | tail -1)"
  export VISUAL_TOKEN
fi

if [ -z "${VISUAL_TOKEN:-}" ]; then
  echo "Could not mint a token. Pass VISUAL_TOKEN=... or check the control container." >&2
  exit 2
fi

exec node "$(dirname "$0")/audit.mjs"
