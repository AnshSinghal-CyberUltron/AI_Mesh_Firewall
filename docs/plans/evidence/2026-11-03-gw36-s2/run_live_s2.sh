#!/usr/bin/env bash
# GW36 S2 live check: console API -> control tables -> projector -> signed state -> gateway reader.
#
# Prereqs: the stack is up with the S2 images, control is migrated, both projectors run, and a
# console JWT is in /tmp/gw36_s2_token (any org-admin user of org `zeroshield`).
# The plaintext gateway key exists only in a shell variable; evidence records its sha256 only.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
E=docs/plans/evidence/2026-11-03-gw36-s2
B=${CONTROL_URL:-http://127.0.0.1:8100}
TOKEN=$(cat /tmp/gw36_s2_token)

api() { curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' "$@"; }
sql() { docker exec ai_mesh_firewall-postgres-1 psql -U ai_mesh_firewall -d ai_mesh_firewall -tAc "$1"; }
verify() { docker compose exec -T state-projector-a python - "$@" < "$E/verify_state.py"; }
now_ms() { date +%s%3N; }

# Wait until amf_state_record holds (kind, key) with the given `deleted` value. Prints the ms.
wait_record() {
  local kind=$1 key=$2 want=$3 start=$4
  for _ in $(seq 1 200); do
    got=$(sql "select deleted from amf_state_record where kind='$kind' and key='$key'")
    if [ "$got" = "$want" ]; then echo $(( $(now_ms) - start )); return 0; fi
    sleep 0.05
  done
  echo "TIMEOUT"; return 1
}

wait_engaged() {
  local scope=$1 want=$2 start=$3
  for _ in $(seq 1 200); do
    got=$(sql "select engaged from amf_state_record where kind='ks' and key='$scope'")
    if [ "$got" = "$want" ]; then echo $(( $(now_ms) - start )); return 0; fi
    sleep 0.05
  done
  echo "TIMEOUT"; return 1
}

{
echo "# step 4: create a key through the console API"
t0=$(now_ms)
created=$(api -X POST "$B/api/gateways/keys/" -d '{"name":"gw36-s2-live","project_id":"gw36-s2"}')
plain=$(echo "$created" | python3 -c "import sys,json;print(json.load(sys.stdin)['key'])")
key_id=$(echo "$created" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
hash=$(printf '%s' "$plain" | sha256sum | cut -d' ' -f1)
unset plain
echo "created key_id=$key_id sha256=${hash:0:12}..."
echo "new-key lag (API 201 -> signed KEY durable): $(wait_record key "$hash" f "$t0") ms"
sleep 1.2
verify org=zeroshield "key=$hash" | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader:', d['key'], d['principals'], 'stamp', d['stamp'])"

echo
echo "# step 5: save the firewall config through the console API"
current=$(api "$B/api/firewall/config/" | python3 -c "import sys,json;print(json.load(sys.stdin)['toxicity_threshold'])")
next=$(python3 -c "print(0.62 if abs(float('$current') - 0.61) < 1e-9 else 0.61)")
plan_head() { sql "select epoch||'.'||seq||':'||feed_seq from amf_state_record where kind='plan' and key='zeroshield'"; }
before=$(plan_head)
saved=$(api -X PUT "$B/api/firewall/config/" -d "{\"toxicity_threshold\": $next}")
echo "toxicity_threshold $current -> $next; console note: $(echo "$saved" | python3 -c "import sys,json;print(json.load(sys.stdin)['gateway_v2_plan'])")"
sleep 1.5
after=$(plan_head)
echo "signed PLAN zeroshield: $before -> $after"
verify org=zeroshield | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader:', d['plan'], d['plans'])"
again=$(api -X PUT "$B/api/firewall/config/" -d "{\"toxicity_threshold\": $next}" | python3 -c "import sys,json;print(json.load(sys.stdin)['gateway_v2_plan']['revision'])")
sleep 1.5
echo "identical save: revision stays $again, signed PLAN $(plan_head) (compare-and-skip: no new version)"

echo
echo "# step 6: deactivate the key through the console API"
t0=$(now_ms)
code=$(api -o /dev/null -w '%{http_code}' -X PATCH "$B/api/gateways/keys/$key_id/" -d '{"is_active": false}')
echo "PATCH is_active=false -> HTTP $code"
echo "revoke lag (API -> signed REVOKE durable): $(wait_record key "$hash" t "$t0") ms"
sql "select op, deleted from amf_state_record where kind='key' and key='$hash'"
sleep 1.2
verify "key=$hash" | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader:', d['principals'])"

echo
echo "# step 7a: the console refuses an org-wide switch; it only creates per-model ones"
api -X POST "$B/api/kill-switches/" -d '{"model_name":"__global__","action":"disable","reason":"gw36 s2"}'
echo
ks=$(api -X POST "$B/api/kill-switches/" -d '{"model_name":"gpt-4o","action":"disable","reason":"gw36 s2 live check"}')
ks_id=$(echo "$ks" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "per-model activate -> HTTP $(api -o /dev/null -w '%{http_code}' -X POST "$B/api/kill-switches/$ks_id/activate/" -d '{"reason":"gw36 s2 live check"}')"
sleep 2
docker exec ai_mesh_firewall-state-projector-a-1 python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:9109/metrics').read().decode())" | grep unsupported_switches
docker logs --since 5s ai_mesh_firewall-state-projector-a-1 2>&1 | grep -m1 "has no v2 scope" | cut -c1-220
echo "KS records (none may exist for a per-model switch): $(sql "select count(*) from amf_state_record where kind='ks'")"
api -o /dev/null -X POST "$B/api/kill-switches/$ks_id/deactivate/" -d '{}'
api -o /dev/null -X DELETE "$B/api/kill-switches/$ks_id/"

echo
echo "# step 7b: an org-wide switch saved through the model (admin / ORM path; the API refuses it)"
ks_pk=$(docker compose exec -T control python manage.py shell -c "
from auth.models import Organization; from core.models import KillSwitch
ks = KillSwitch.objects.create(organization=Organization.objects.get(slug='zeroshield'), model_name='__global__', action='disable', is_active=False, reason='gw36 s2')
print('PK', ks.pk)" 2>/dev/null | grep '^PK' | cut -d' ' -f2)
t0=$(now_ms)
docker compose exec -T control python manage.py shell -c "
from core.models import KillSwitch; ks = KillSwitch.objects.get(pk=$ks_pk); ks.is_active = True; ks.save()" >/dev/null 2>&1
echo "kill-switch-on lag (save -> engaged KS durable): $(wait_engaged org:zeroshield t "$t0") ms"
sleep 1.2
verify org=zeroshield | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader killed:', d['killed'], d['ks'])"
t0=$(now_ms)
docker compose exec -T control python manage.py shell -c "
from core.models import KillSwitch; ks = KillSwitch.objects.get(pk=$ks_pk); ks.is_active = False; ks.save()" >/dev/null 2>&1
echo "kill-switch-off lag: $(wait_engaged org:zeroshield f "$t0") ms"
sleep 1.2
verify org=zeroshield | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader killed:', d['killed'])"
docker compose exec -T control python manage.py shell -c "
from core.models import KillSwitch; KillSwitch.objects.filter(pk=$ks_pk).delete()" >/dev/null 2>&1

echo
echo "# step 8: hard-delete a second key (the console's revoke button)"
created=$(api -X POST "$B/api/gateways/keys/" -d '{"name":"gw36-s2-delete","project_id":"gw36-s2"}')
plain=$(echo "$created" | python3 -c "import sys,json;print(json.load(sys.stdin)['key'])")
key2=$(echo "$created" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
hash2=$(printf '%s' "$plain" | sha256sum | cut -d' ' -f1)
unset plain
wait_record key "$hash2" f "$(now_ms)" >/dev/null
t0=$(now_ms)
echo "DELETE -> HTTP $(api -o /dev/null -w '%{http_code}' -X DELETE "$B/api/gateways/keys/$key2/")"
echo "delete->revoke lag: $(wait_record key "$hash2" t "$t0") ms"
} 2>&1 | tee "$E/04_console_scenarios.txt"
