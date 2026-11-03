#!/usr/bin/env bash
# GW36 S2 live drill: writer failover, then both projectors down (stamp withheld), then recovery.
set -euo pipefail
cd "$(dirname "$0")/../../../.."
E=docs/plans/evidence/2026-11-03-gw36-s2

metric() {  # container port name
  docker exec "$1" python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:$2/metrics').read().decode())" \
    | awk -v n="$3" '$1 == n {print $2}'
}
now_ms() { date +%s%3N; }
withheld() { metric ai_mesh_firewall-state-rehydrator-a-1 9108 amf_state_stamp_withheld_source_lag_total; }
holder() { docker exec ai_mesh_firewall-postgres-1 psql -U ai_mesh_firewall -d ai_mesh_firewall -tAc \
  "select holder||' live='||(lease_until > clock_timestamp())||' lag_s='||round(extract(epoch from clock_timestamp()-last_ok_at)::numeric,2) from amf_state_projector"; }

{
echo "# step 9: writer failover (stop projector-a; projector-b must take over within the lease)"
echo "lease before: $(holder)"
base=$(withheld)
t0=$(now_ms)
docker stop ai_mesh_firewall-state-projector-a-1 >/dev/null
for _ in $(seq 1 200); do
  case "$(holder)" in projector-b*) break;; esac
  sleep 0.1
done
echo "takeover after $(( $(now_ms) - t0 )) ms: $(holder)"
sleep 3
echo "stamps withheld for source lag during failover: $(( $(withheld | cut -d. -f1) - ${base%.*} ))"
echo "projector-b leader metric: $(metric ai_mesh_firewall-state-projector-b-1 9109 amf_projector_leader)"

echo
echo "# step 10: both projectors down -> stamp withheld once the heartbeat is older than 16 s"
base=$(withheld)
t0=$(now_ms)
docker stop ai_mesh_firewall-state-projector-b-1 >/dev/null
for _ in $(seq 1 300); do
  [ "$(( $(withheld | cut -d. -f1) - ${base%.*} ))" -gt 0 ] && break
  sleep 0.2
done
echo "first withheld stamp $(( $(now_ms) - t0 )) ms after the last projector stopped: $(holder)"
docker logs --since 5s ai_mesh_firewall-state-rehydrator-a-1 2>&1 | grep -m1 "withheld" | cut -c1-200
sleep 6
docker compose exec -T state-rehydrator-a python - < "$E/verify_state.py" \
  | python3 -c "import sys,json;print('gateway reader stamp after 6 s more:', json.load(sys.stdin)['stamp'])"

echo
echo "# step 11: recovery"
t0=$(now_ms)
docker start ai_mesh_firewall-state-projector-a-1 ai_mesh_firewall-state-projector-b-1 >/dev/null
for _ in $(seq 1 300); do
  if docker logs --since 30s ai_mesh_firewall-state-rehydrator-a-1 2>&1 | grep -q "freshness stamp resumed"; then break; fi
  sleep 0.2
done
echo "stamp resumed $(( $(now_ms) - t0 )) ms after the projectors were started: $(holder)"
sleep 2
docker compose exec -T state-rehydrator-a python - < "$E/verify_state.py" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('gateway reader:', d['stamp'], 'plan', d['plan']['ok'], 'key', d['key']['ok'], 'ks', d['ks']['ok'])"
} 2>&1 | tee "$E/05_failover_and_outage.txt"
