#!/usr/bin/env bash
# P0.0 orchestrator. Never touches aimeshperf / ai_mesh_firewall.
set -euo pipefail
ROOT="/home/contact_cyberultron_com/aimesh-p0-task0"
cd "$ROOT"
export COMPOSE_PROJECT_NAME=aimf_p0
export P0_GIT_SHA
P0_GIT_SHA="$(git rev-parse HEAD)"
export GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:18300}"
EVIDENCE="$ROOT/docs/perf/evidence/2026-09-10-p0-task0-honesty"
COMPOSE=(docker compose --project-name aimf_p0 -f docker-compose.yml -f scripts/perf/e2e/compose.p0.yml)

if [[ "$GATEWAY_URL" == *"127.0.0.1:8300"* ]] || [[ "$GATEWAY_URL" == *"localhost:8300"* ]]; then
  echo "refuse: GATEWAY_URL is live :8300" >&2
  exit 2
fi

# Refuse operating on the live perf / product projects.
if docker ps --format '{{.Names}}' | grep -qx 'aimeshperf-gateway-1'; then
  echo "note: aimeshperf-gateway-1 is running and will be left untouched"
fi

for n in aimeshperf-gateway-1 ai_mesh_firewall-gateway-1; do
  if [[ "${P0_ALLOW_LIVE_MUTATION:-}" == "1" ]]; then
    echo "refuse: P0_ALLOW_LIVE_MUTATION is not supported" >&2
    exit 2
  fi
  :
done

mkdir -p "$EVIDENCE/cells"
chmod 700 "$EVIDENCE" || true

echo "== down -v aimf_p0 =="
"${COMPOSE[@]}" down -v --remove-orphans

echo "== build --no-cache gateway+control =="
"${COMPOSE[@]}" build --no-cache gateway control

echo "== up postgres redis pgbouncer (empty-redis check before control can republish) =="
"${COMPOSE[@]}" up -d postgres redis pgbouncer
for i in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then break; fi
  sleep 2
done
stale="$("${COMPOSE[@]}" exec -T redis redis-cli --scan --pattern 'firewall:config*' ; \
         "${COMPOSE[@]}" exec -T redis redis-cli --scan --pattern 'policies:compiled*' ; \
         "${COMPOSE[@]}" exec -T redis redis-cli --scan --pattern 'llm:model_configs*' ; \
         "${COMPOSE[@]}" exec -T redis redis-cli --scan --pattern 'kill_switch:*' || true)"
stale="$(printf '%s\n' "$stale" | sed '/^$/d')"
if [[ -n "$stale" ]]; then
  echo "refuse: Redis not empty before seed:" >&2
  echo "$stale" >&2
  exit 1
fi

echo "== up control gateway =="
"${COMPOSE[@]}" up -d control gateway

echo "== wait control+gateway healthy =="
for i in $(seq 1 90); do
  cs="$("${COMPOSE[@]}" ps --format json 2>/dev/null | python3 -c 'import json,sys
rows=[]
raw=sys.stdin.read().strip()
if not raw: raise SystemExit(1)
# compose may emit NDJSON
for line in raw.splitlines():
    try: rows.append(json.loads(line))
    except Exception: pass
if not rows:
    try: rows=json.loads(raw)
    except Exception: raise SystemExit(1)
if isinstance(rows, dict): rows=[rows]
ok=0
for r in rows:
    name=str(r.get("Name") or r.get("Service") or "")
    h=str(r.get("Health") or "")
    s=str(r.get("State") or r.get("Status") or "")
    if "control" in name and h=="healthy": ok+=1
    if "gateway" in name and h=="healthy": ok+=1
print(ok)
' || echo 0)"
  if [[ "$cs" == "2" ]]; then
    break
  fi
  sleep 4
done

peer="$("${COMPOSE[@]}" ps -q gateway)"
proj="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$peer")"
if [[ "$proj" != "aimf_p0" ]]; then
  echo "refuse: gateway project is $proj" >&2
  exit 2
fi

echo "== seed org aimfp0 =="
"${COMPOSE[@]}" exec -T control python manage.py migrate --noinput
"${COMPOSE[@]}" exec -T control python manage.py ensure_zeroshield_admin --org-slug aimfp0 --org-name AIMFP0
"${COMPOSE[@]}" exec -T control python manage.py seed_pii_policy_package --org-slug aimfp0
echo "== seed org inference model (stub still requires BYOK catalog) =="
"${COMPOSE[@]}" exec -T control python manage.py shell -c "$(cat "$ROOT/scripts/perf/e2e/p0_seed_inference.py")"
"${COMPOSE[@]}" exec -T control python manage.py shell -c "
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey
User = get_user_model()
org = Organization.objects.get(slug='aimfp0')
user = User.objects.filter(email='admin@zeroshield.io').first() or User.objects.first()
inst, raw = GatewayAPIKey.ensure_simulator_for_org(org, user)
print(raw)
" | tail -n 1 | tr -d '\r' > "$EVIDENCE/.api_key"
chmod 600 "$EVIDENCE/.api_key"
export P0_API_KEY
P0_API_KEY="$(cat "$EVIDENCE/.api_key")"

echo "== restart gateway after seed =="
"${COMPOSE[@]}" restart gateway
for i in $(seq 1 60); do
  if curl -fsS "$GATEWAY_URL/health" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== preflight RAM+probe =="
python3 scripts/perf/e2e/p0_preflight.py

echo "== Cell_A =="
python3 scripts/perf/e2e/p0_drive.py --cell A --n 16 --warmup 2

echo "== block / redact / size =="
python3 scripts/perf/e2e/p0_drive.py --cell block --n 8 --warmup 1 || true
python3 scripts/perf/e2e/p0_drive.py --cell redact --n 8 --warmup 1 || true
python3 scripts/perf/e2e/p0_drive.py --cell size --n 8 --warmup 1 || true

echo "== post-run Redis+RAM re-GET =="
python3 scripts/perf/e2e/p0_preflight.py --post-run
python3 scripts/perf/e2e/p0_write_pack.py

echo "== done =="
ls -la "$EVIDENCE" "$EVIDENCE/cells"
