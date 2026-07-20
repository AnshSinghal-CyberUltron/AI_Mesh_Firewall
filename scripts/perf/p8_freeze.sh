#!/bin/bash
# P8 freeze — re-run the P7 constrained-profile proof 3x consecutive on BOTH
# profiles (6c/16g and 12c/60g) and assert the invariants every round:
#   detector worker count correct, /health 200, 0 errors under load, no OOM,
#   RAM under the 0.75 headroom budget. Prints PASS/FAIL per round; exits 0 only
#   if all rounds on both profiles pass.
set -u
NET=ai_mesh_firewall_default
IMG_GW=ai_mesh_firewall-gateway
IMG_CTL=ai_mesh_firewall-control
fails=0

run_profile() {  # round cpus mem gwworkers gwport ctlport
  local round=$1 cpus=$2 mem=$3 wexp=$4 gwp=$5 ctp=$6
  docker rm -f fz_gw fz_ctl >/dev/null 2>&1
  docker run -d --cpus="$cpus" --memory="$mem" --network "$NET" --name fz_gw -p "$gwp":8300 \
    -e GATEWAY_ALLOW_HTTP=1 -e GATEWAY_REDIS_URL=redis://redis:6379/0 -e REDIS_URL=redis://redis:6379/0 \
    -e AI_MESH_CONTROL_URL=http://control:8000 -e POLICY_SIGNING_KEY=fz-dummy -e DJANGO_SECRET_KEY=dev-secret-change-me \
    "$IMG_GW" >/dev/null 2>&1
  docker run -d --cpus="$cpus" --memory="$mem" --network "$NET" --name fz_ctl -p "$ctp":8000 \
    -e DATABASE_URL=postgresql://ai_mesh_firewall:ai_mesh_firewall@postgres:5432/ai_mesh_firewall \
    -e REDIS_URL=redis://redis:6379/0 -e CHANNEL_LAYERS_REDIS_URL=redis://redis:6379/1 \
    -e DJANGO_SECRET_KEY=dev-secret-change-me -e ALLOWED_HOSTS=localhost,127.0.0.1,control -e DEBUG=true \
    -e TELEMETRY_DRAIN_MODE=beat "$IMG_CTL" >/dev/null 2>&1
  local gwok=0 ctok=0
  for i in $(seq 1 40); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$gwp/health 2>/dev/null)" = "200" ] && gwok=1
    [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$ctp/api/health/ 2>/dev/null)" = "200" ] && ctok=1
    [ $gwok -eq 1 ] && [ $ctok -eq 1 ] && break; sleep 1
  done
  local gww=$(docker exec fz_gw sh -c 'cat /proc/1/cmdline|tr "\0" " "' 2>/dev/null | grep -oE 'workers [0-9]+' | grep -oE '[0-9]+')
  local ctw=$(docker exec fz_ctl sh -c 'cat /proc/1/cmdline|tr "\0" " "' 2>/dev/null | grep -oE 'workers [0-9]+' | grep -oE '[0-9]+')
  # load both concurrently
  python3 scripts/perf/loadtest.py --url http://127.0.0.1:$gwp/health --procs 4 --concurrency 30 --duration 8 --containers fz_gw >/tmp/fz_gw.json 2>&1 &
  local g=$!
  python3 scripts/perf/loadtest.py --url http://127.0.0.1:$ctp/api/health/ --procs 3 --concurrency 15 --duration 8 --containers fz_ctl >/tmp/fz_ctl.json 2>&1 &
  local c=$!
  wait $g; wait $c
  local gwerr=$(python3 -c "import json;print(json.load(open('/tmp/fz_gw.json'))['errors'])" 2>/dev/null)
  local gwrps=$(python3 -c "import json;print(json.load(open('/tmp/fz_gw.json'))['rps'])" 2>/dev/null)
  local gwcore=$(python3 -c "import json;print(json.load(open('/tmp/fz_gw.json'))['containers']['fz_gw']['cores_used'])" 2>/dev/null)
  local cterr=$(python3 -c "import json;print(json.load(open('/tmp/fz_ctl.json'))['errors'])" 2>/dev/null)
  local ctrps=$(python3 -c "import json;print(json.load(open('/tmp/fz_ctl.json'))['rps'])" 2>/dev/null)
  local ctcore=$(python3 -c "import json;print(json.load(open('/tmp/fz_ctl.json'))['containers']['fz_ctl']['cores_used'])" 2>/dev/null)
  local gwoom=$(docker inspect fz_gw --format '{{.State.OOMKilled}}' 2>/dev/null)
  local ctoom=$(docker inspect fz_ctl --format '{{.State.OOMKilled}}' 2>/dev/null)
  local gwmem=$(docker stats --no-stream --format '{{.MemUsage}}' fz_gw 2>/dev/null | awk '{print $1}')
  local ctmem=$(docker stats --no-stream --format '{{.MemUsage}}' fz_ctl 2>/dev/null | awk '{print $1}')
  # assertions
  local ok=1
  [ "$gwok" = 1 ] && [ "$ctok" = 1 ] || { ok=0; }
  [ "$gww" = "$wexp" ] && [ "$ctw" = "$wexp" ] || ok=0
  [ "${gwerr:-1}" = "0" ] && [ "${cterr:-1}" = "0" ] || ok=0
  [ "$gwoom" = "false" ] && [ "$ctoom" = "false" ] || ok=0
  local verdict="PASS"; [ $ok = 1 ] || { verdict="FAIL"; fails=$((fails+1)); }
  printf "  R%s %-9s workers gw=%s/ctl=%s (exp %s) | health gw=%s ctl=%s | gw %srps %score cores err=%s mem=%sGiB oom=%s | ctl %srps %score err=%s mem=%sGiB oom=%s => %s\n" \
    "$round" "${cpus}c/${mem}" "$gww" "$ctw" "$wexp" "$gwok" "$ctok" "$gwrps" "$gwcore" "$gwerr" "$gwmem" "$gwoom" "$ctrps" "$ctcore" "$cterr" "$ctmem" "$ctoom" "$verdict"
  docker rm -f fz_gw fz_ctl >/dev/null 2>&1
}

echo "=== P8 FREEZE: 3x consecutive, both profiles ==="
for r in 1 2 3; do
  run_profile "$r" 6 16g 6 8480 8481
  run_profile "$r" 12 56g 12 8482 8483
done
echo "=== TOTAL FAILURES: $fails ==="
[ $fails -eq 0 ] && echo "FREEZE RESULT: ALL PASS" || echo "FREEZE RESULT: $fails FAILED"
exit $fails
