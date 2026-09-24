#!/usr/bin/env bash
# chainB.sh: phase B (asia-south1-b) scaling curve under the C4 rule (C4 p99 < 20 ms, infra <= 0.1%, 0 drops),
# 3 runs at each knee:
#   B1 (a) 1 gateway + 1 guard: one confirmation run at the phase-A knee (191)
#   B2 (b) 1 gateway + 2 guards: knee search from 298 + 2 repeats; one run with the metrics dump disabled at 373
#   B3 (c) 2 gateways + 2 guards behind the nginx edge, shared redis: ladder + repeats
#   B4 (d) the <= $5k fleet: 3 gateways + 5 guards behind the edge, shared redis: ladder + repeats
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; T=$E/tools
RUNS=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs; LOGS=$(dirname "$RUNS")/logs
G1=10.160.0.10:7070 G2=10.160.15.248:7070 G3=10.160.15.247:7070 G4=10.160.15.246:7070 G5=10.160.15.245:7070
v() { python3 -c "import json; d=json.load(open('$RUNS/$1/verdict.json')); print(d['verdict'], 'q/s', d['qualified_rps'], 'C4', d['c4_p99'], 'infra%', d['infra_pct'], 'drops', d['drops'], 'json', d['json_total_p99'], 'sse-total', d['sse_total_p99'])" 2>/dev/null || echo NO_VERDICT; }
step() { local name=$1 rate=$2; shift 2
  [[ -d "$RUNS/$name" ]] && { echo "$(date -u +%T) SKIP $name (exists): $(v "$name")"; return; }
  echo "$(date -u +%T) START $name rate=$rate env=[$*]"
  env "$@" bash "$T/split_step.sh" "$name" "$rate" headline-22M.jsonl > "$LOGS/$name.log" 2>&1
  echo "$(date -u +%T) END $name $(v "$name")"; }
passed() { [[ $(v "$1") == PASS* ]]; }
# ladder_knee PREFIX "UP RATES" "FALLBACK RATES (descending)" ENV...: stop at the first FAIL; knee = last PASS;
# 2 repeats at the knee; if a repeat fails, step down to the previous passing rate and repeat there.
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
A1=(GWS=rv-split-gw-1 GUARDS=rv-split-guard-1 PROVS=rv-split-prov-1 LGS=rv-split-lg-1)
echo "$(date -u +%T) === B1 (a) 1gw+1guard confirmation (zone b)"
step s1b-191 191 LABEL=a_zone_b_confirm "${A1[@]}"
echo "$(date -u +%T) === B2 (b) 1gw+2guards"
bash "$T/gw_config.sh" b-b-1gw2g "rv-split-gw-1" "$G1,$G2" local "rv-split-prov-1" | tail -1
B2=(GWS=rv-split-gw-1 "GUARDS=rv-split-guard-1 rv-split-guard-2" PROVS=rv-split-prov-1 LGS=rv-split-lg-1)
ladder_knee s2b "298 373" "238 191" LABEL=b_1gw2g "${B2[@]}"
NODUMP=1 bash "$T/gw_config.sh" b-b-1gw2g-nodump "rv-split-gw-1" "$G1,$G2" local "rv-split-prov-1" | tail -1
step s2b-373-nodump 373 LABEL=b_1gw2g_metrics_dump_disabled "${B2[@]}"
echo "$(date -u +%T) === B3 (c) 2gw+2guards + edge + shared redis"
bash "$T/gw_config.sh" c-2gw2g "rv-split-gw-1 rv-split-gw-2" "$G1,$G2" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" | tail -2
bash "$T/edge_config.sh" c-2gw2g "rv-split-gw-1 rv-split-gw-2" | tail -2
C3=(TARGETS=http://10.160.0.11:8080 "GWS=rv-split-gw-1 rv-split-gw-2" "GUARDS=rv-split-guard-1 rv-split-guard-2"
    "PROVS=rv-split-prov-1 rv-split-prov-2" "LGS=rv-split-lg-1 rv-split-lg-2" "EXTRA_SUT=rv-split-edge-1 rv-split-redis-1")
ladder_knee s3b "373 466 582" "298 238" LABEL=c_2gw2g_edge "${C3[@]}"
echo "$(date -u +%T) === B4 (d) <=\$5k fleet: 3gw+5guards + edge + shared redis"
bash "$T/gw_config.sh" d-3gw5g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" "$G1,$G2,$G3,$G4,$G5" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" | tail -3
bash "$T/edge_config.sh" d-3gw5g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" | tail -2
D4=(TARGETS=http://10.160.0.11:8080 "GWS=rv-split-gw-1 rv-split-gw-2 rv-split-gw-3"
    "GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5"
    "PROVS=rv-split-prov-1 rv-split-prov-2" "LGS=rv-split-lg-1 rv-split-lg-2" "EXTRA_SUT=rv-split-edge-1 rv-split-redis-1")
ladder_knee s5b "728 910 1137" "582 466" LABEL=d_fleet_3gw5g_edge "${D4[@]}"
echo "$(date -u +%T) CHAINB DONE"
