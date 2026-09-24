#!/usr/bin/env bash
# chainC.sh (after chainB): C4 rule (C4 p99 < 20 ms, infra <= 0.1%, 0 drops), configuration only (rvproto-frozen-1).
#   C1 one Poisson-arrival run at the (d) fleet knee (466) on the unchanged 3gw+5g config: is the fleet knee
#      robust to the arrival pattern? (the 1-guard constant knee 191 was not: Poisson failed down to 122)
#   C2 resized <= $5k fleet: at the (d) knee every gateway ran ~1.9 of 16 vCPU busy while the guards bound, so
#      gw-3 is swapped for a 6th guard (2 gateways + 6 guards = $4,766.86/month): ladder 582 -> 728, 3/3 at the knee.
#      If no 6th g2-standard-4 could be obtained (stock-out), measure the largest subset instead:
#      2 gateways + 5 guards ($4,202.09/month) at the (d) knee, 3 runs.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; T=$E/tools
export LANE=split EVID=$E; source "$SP/harness/deploy/lib.sh"
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs; LOGS=$(dirname "$RUNS")/logs
G1=10.160.0.10:7070 G2=10.160.15.248:7070 G3=10.160.15.247:7070 G4=10.160.15.246:7070 G5=10.160.15.245:7070
v() { python3 -c "import json; d=json.load(open('$RUNS/$1/verdict.json')); print(d['verdict'], 'q/s', d['qualified_rps'], 'C4', d['c4_p99'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'json', d['json_total_p99'], 'sse-total', d['sse_total_p99'])" 2>/dev/null || echo NO_VERDICT; }
step() { local name=$1 rate=$2; shift 2
  [[ -d "$RUNS/$name" ]] && { echo "$(date -u +%T) SKIP $name (exists): $(v "$name")"; return; }
  echo "$(date -u +%T) START $name rate=$rate env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" headline-22M.jsonl > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(v "$name")"; }
passed() { [[ $(v "$1") == PASS* ]]; }
ladder_knee() {
  local p=$1 up=$2 down=$3; shift 3
  local passes=() r
  for r in $up; do step "$p-$(printf %03d $r)" "$r" "$@"; if passed "$p-$(printf %03d $r)"; then passes+=("$r"); else break; fi; done
  if [[ ${#passes[@]} == 0 ]]; then
    for r in $down; do step "$p-$(printf %03d $r)" "$r" "$@"; if passed "$p-$(printf %03d $r)"; then passes=("$r"); break; fi; done
  fi
  while [[ ${#passes[@]} -gt 0 ]]; do
    local k=${passes[-1]} k3; k3=$(printf %03d "${passes[-1]}")
    step "$p-$k3-r2" "$k" "$@"; step "$p-$k3-r3" "$k" "$@"
    if passed "$p-$k3-r2" && passed "$p-$k3-r3"; then echo "$(date -u +%T) KNEE $p = $k (3/3 PASS)"; return; fi
    unset 'passes[-1]'
    if [[ ${#passes[@]} == 0 ]]; then
      for r in $down; do [[ $r -lt $k ]] || continue; step "$p-$(printf %03d $r)" "$r" "$@"; passed "$p-$(printf %03d $r)" && { passes=("$r"); break; }; done
    fi
  done
  echo "$(date -u +%T) KNEE $p = none"
}
until grep -q "CHAINB DONE" "$E/notes/chainB.log"; do sleep 10; done
COMMON=(TARGETS=http://10.160.0.11:8080 "PROVS=rv-split-prov-1 rv-split-prov-2" "LGS=rv-split-lg-1 rv-split-lg-2" "EXTRA_SUT=rv-split-edge-1 rv-split-redis-1")
echo "$(date -u +%T) === C1 (d) 3gw+5g Poisson arrivals at the fleet knee"
step s5bp-466 466 "OLG_EXTRA=-sample-mod 1 -arrival poisson" LABEL=d_fleet_3gw5g_edge_poisson "GWS=rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" \
  "GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5" "${COMMON[@]}"
until [[ -f $E/.guard6-ready || -f $E/.guard6-none ]]; do sleep 10; done
rssh rv-split-gw-3 'bash ~/rv/rvproto/deploy/stop_unit.sh >/dev/null 2>&1; echo gw-3 stopped'
if [[ -f $E/.guard6-ready ]]; then
  G6=$(cat "$E/.guard6-ready")
  echo "$(date -u +%T) === C2 resized <=\$5k fleet: 2gw+6g (guard-6 $G6) + edge + shared redis"
  bash "$T/gw_config.sh" d2-2gw6g "rv-split-gw-1 rv-split-gw-2" "$G1,$G2,$G3,$G4,$G5,$G6" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" | tail -2
  bash "$T/edge_config.sh" d2-2gw6g "rv-split-gw-1 rv-split-gw-2" | tail -2
  ladder_knee s6b "582 728" "466" LABEL=d2_fleet_2gw6g_edge "GWS=rv-split-gw-1 rv-split-gw-2" \
    "GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5 rv-split-guard-6" "${COMMON[@]}"
else
  echo "$(date -u +%T) === C2 no 6th G2 ($(cat "$E/.guard6-none")): largest subset 2gw+5g + edge + shared redis"
  bash "$T/gw_config.sh" d3-2gw5g "rv-split-gw-1 rv-split-gw-2" "$G1,$G2,$G3,$G4,$G5" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" | tail -2
  bash "$T/edge_config.sh" d3-2gw5g "rv-split-gw-1 rv-split-gw-2" | tail -2
  ladder_knee s25b "466" "373" LABEL=d3_subset_2gw5g_edge "GWS=rv-split-gw-1 rv-split-gw-2" \
    "GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5" "${COMMON[@]}"
fi
echo "$(date -u +%T) CHAINC DONE"
