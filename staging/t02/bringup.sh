#!/usr/bin/env bash
# T02 bring-up after a Docker wipe. Does not print secrets.
set -euo pipefail
REPO="${REPO:-/home/contact_cyberultron_com/AI_Mesh_Firewall}"
cd "$REPO"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.t02.yml)
SECRETS=/tmp/t02-secrets.env
PRESERVE="${HOME}/.t02-preserve"

if [[ ! -f "$REPO/.env" && -f "$PRESERVE/.env" ]]; then
  cp -a "$PRESERVE/.env" "$REPO/.env"
  chmod 600 "$REPO/.env"
fi
if [[ ! -f "$SECRETS" ]]; then
  umask 077
  rec=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  adm=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  printf 'T02_RECORDER_KEY=%s\nT02_ADMIN_TOKEN=%s\n' "$rec" "$adm" >"$SECRETS"
  chmod 600 "$SECRETS"
fi
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a
export T02_RECORDER_KEY T02_ADMIN_TOKEN

bash scripts/ensure_mcp_sandbox_network.sh
bash scripts/ensure_org_sandbox_network.sh

echo "[t02] building control/gateway/recorder (this takes several minutes)"
"${COMPOSE[@]}" build control gateway t02-recorder

if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm ci)
fi
(cd frontend && npm run build)
GIT_SHA=$(git rev-parse HEAD)
docker build -f staging/t02/Dockerfile.nginx --build-arg GIT_SHA="$GIT_SHA" -t "ai-mesh-nginx-t00:t02" .

echo "[t02] starting stack"
"${COMPOSE[@]}" up -d postgres redis pgbouncer rabbitmq
"${COMPOSE[@]}" up -d control gateway t02-recorder demo mcp-stub frontend

echo "[t02] waiting for health"
for i in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8100/api/health/ >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8300/health >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:18081/health >/dev/null 2>&1; then
    echo "[t02] health ok at try $i"
    break
  fi
  sleep 4
  if [[ "$i" -eq 90 ]]; then
    echo "[t02] health wait exhausted" >&2
    "${COMPOSE[@]}" ps
    exit 1
  fi
done

echo "[t02] migrate + seed"
"${COMPOSE[@]}" exec -T control python manage.py migrate --noinput
"${COMPOSE[@]}" exec -T control python manage.py ensure_zeroshield_admin --reset-password
docker cp staging/t02/provision.py ai_mesh_firewall-control-1:/tmp/provision_t02.py
"${COMPOSE[@]}" exec -T -e T02_RECORDER_KEY -e T02_RECORDER_API_BASE=http://t02-recorder:8080/v1 \
  control python /tmp/provision_t02.py
docker cp ai_mesh_firewall-control-1:/tmp/t02.keys.json /tmp/t02.keys.json
chmod 600 /tmp/t02.keys.json
echo "[t02] keys written /tmp/t02.keys.json (mode 0600, last4 only in provision stdout)"
