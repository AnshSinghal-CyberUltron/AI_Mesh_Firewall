#!/usr/bin/env bash
# honesty_run.sh PREFIX LG PROV PROXY [RATE]
#   Instrument-honesty suite (HARNESS_SPEC.md §5) on GCP: loadgen LG -> [rvproxy on PROXY] -> synthprov on PROV,
#   every stream sampled (-sample-mod 1 on both sides), WORST-BAND corpus (400 tokens so hold-at 50 applies).
#   Cases: direct floor, proxy floor, 5 ms pre-dispatch delay, 30 ms hold at content event 50, both, and a
#   provider TTFT sweep (50 / 500 / 2000 ms) through the delay-free proxy. Then honesty.py compares the harness
#   metrics with the delays the proxy actually injected (its own log).
#   env: LANE, EVID, RUNS
source "$(dirname "$0")/lib.sh"
prefix=$1 lg=$2 prov=$3 proxy=$4 rate=${5:-300}
RUNS=${RUNS:-$EVID/runs}
SKIP=${SKIP:-}   # space-separated case names to skip (already done)
logd="$EVID/logs"; mkdir -p "$logd"
olg="-corpus /home/rv/rv/corpora/worst-22M.jsonl -ramp 10 -warmup 20 -duration 90 -sample-mod 1"
provip=$(vm_ip "$prov"); proxyip=$(vm_ip "$proxy")
AF="--profile-stages proxy"
case_proxy() { # name proxyflags provflags   (output to $logd/<run>.log; never pipe step.sh into head: SIGPIPE + pipefail)
  local name=$1 pflags=$2 provflags=$3
  [[ " $SKIP " == *" $name "* ]] && { log "skip $name"; return; }
  "$HARNESS/deploy/proxy_start.sh" "$proxy" "$prefix-$name" "http://$provip:8080" $pflags > "$logd/$prefix-$name.log" 2>&1
  TARGETS="http://$proxyip:9000" PROXY_VM="$proxy" ANALYZE_FLAGS="$AF" \
    "$HARNESS/deploy/step.sh" "$prefix-$name" "$rate" "$lg" "$prov" "$olg" "-sample-mod 1 $provflags" >> "$logd/$prefix-$name.log" 2>&1
  log "$name: $(head -1 "$RUNS/$prefix-$name/analysis/summary.md")"
}
unset TARGETS PROXY_VM
if [[ " $SKIP " != *" direct "* ]]; then
  "$HARNESS/deploy/step.sh" "$prefix-direct" "$rate" "$lg" "$prov" "$olg" "-sample-mod 1" > "$logd/$prefix-direct.log" 2>&1
fi
case_proxy proxy0 "" ""
case_proxy pre5 "-pre-delay 5ms" ""
case_proxy hold30 "-hold-at 50 -hold 30ms" ""
case_proxy both "-pre-delay 5ms -hold-at 50 -hold 30ms" ""
case_proxy ttft50 "" "-ttft 50ms"
case_proxy ttft500 "" "-ttft 500ms"
case_proxy ttft2000 "" "-ttft 2000ms"
/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python "$HARNESS/honesty.py" \
  --direct "$RUNS/$prefix-direct" --floor "$RUNS/$prefix-proxy0" --pre "$RUNS/$prefix-pre5" --hold "$RUNS/$prefix-hold30" \
  --both "$RUNS/$prefix-both" --ttft "$RUNS/$prefix-ttft50" "$RUNS/$prefix-ttft500" "$RUNS/$prefix-ttft2000" \
  --out "$RUNS/$prefix-report"
