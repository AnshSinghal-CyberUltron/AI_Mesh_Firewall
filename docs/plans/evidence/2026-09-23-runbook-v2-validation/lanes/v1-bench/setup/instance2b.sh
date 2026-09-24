#!/usr/bin/env bash
# instance2b.sh — NOT-full-profile attribution runs on instance 2 (after instance2.sh):
#   JSON-r10  : full profile, 100% JSON at 10 RPS (JSON-path latency + CPU ms/req; pairs with ITL20-sse-r10)
#   GO-r10    : GATEWAY_OUTPUT_GUARD_ENABLED=false (OutputGuard not constructed; SSE still wrapped by
#               SecureStreamingResponse in scanner_only mode — same holdback, InputScanner.scan_output)
#   FWOFF-r10 : FirewallConfig.firewall_enabled=False (all scanning skipped; v1 proxy path only)
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
export LANE=v1 EVID=$E
source $SP/harness/deploy/lib.sh
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
SUT=rv-v1-sut-2
step() {  # step RUN RATE RAMP WARM DUR [EXTRA olg flags]
  local run=$1 rate=$2 ramp=$3 warm=$4 dur=$5; shift 5
  echo "$(date -u +%T) START $run rate=$rate extra='$*'"
  RAMP_S=$ramp PROV_FLAGS="-ttft 150ms -itl 20ms" bash $E/setup/run_step.sh sut "$run" "$rate" "$warm" "$dur" headline-22M.jsonl "$@" > "$E/runs/$run.log" 2>&1
  echo "$(date -u +%T) DONE $run"
  $PY $E/setup/ladder_table.py "$run" 2>&1 | tail -1
}
recreate_gateway() {  # recreate_gateway true|false
  rssh $SUT "cd ~/v1 && grep -q V1B_OUTPUT_GUARD compose.v1bench.yml || sed -i 's/GATEWAY_OUTPUT_GUARD_ENABLED: \"true\"/GATEWAY_OUTPUT_GUARD_ENABLED: \"\${V1B_OUTPUT_GUARD:-true}\"/' compose.v1bench.yml; \
    V1B_OUTPUT_GUARD=$1 sudo -E docker compose -f docker-compose.yml -f compose.v1bench.yml up -d --force-recreate gateway >/dev/null 2>&1; \
    for i in \$(seq 1 60); do s=\$(sudo docker inspect -f '{{.State.Health.Status}}' aimeshperf-gateway-1); [ \"\$s\" = healthy ] && break; sleep 3; done; \
    echo gateway=\$s GATEWAY_OUTPUT_GUARD_ENABLED=\$(sudo docker exec aimeshperf-gateway-1 printenv GATEWAY_OUTPUT_GUARD_ENABLED); \
    sudo docker logs aimeshperf-gateway-1 2>&1 | grep -E 'WEB_CONCURRENCY=|Output guard initialized' | sort | uniq -c"
}
echo "$(date -u +%T) recreate gateway, full profile (fresh workers after the overload runs)"; recreate_gateway true
step warmup-json 20 10 0 60
step JSON-r10 10 10 30 180 -sse-frac 0.0
echo "$(date -u +%T) recreate gateway with output guard OFF"; recreate_gateway false
step warmup-go 20 10 0 60
step GO-r10 10 30 60 300
echo "$(date -u +%T) recreate gateway with output guard ON (restore full profile)"; recreate_gateway true
rssh $SUT "cd ~/v1 && sudo docker cp ~/set_firewall_enabled.py aimeshperf-control-1:/tmp/set_firewall_enabled.py && sudo docker compose -f docker-compose.yml -f compose.v1bench.yml exec -T -e V1B_FW=off control python /tmp/set_firewall_enabled.py 2>&1 | grep '^\[fw\]'"
sleep 5
step warmup-fwoff 20 10 0 60
step FWOFF-r10 10 30 60 300
rssh $SUT "cd ~/v1 && sudo docker compose -f docker-compose.yml -f compose.v1bench.yml exec -T -e V1B_FW=on control python /tmp/set_firewall_enabled.py 2>&1 | grep '^\[fw\]'"
echo "$(date -u +%T) ALL DONE (profile restored: output guard on, firewall on)"
