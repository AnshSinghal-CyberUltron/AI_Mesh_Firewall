#!/usr/bin/env bash
# step.sh RUN TOTAL_RATE "LG1 LG2 .." "PROV1 PROV2 .." "OLG_FLAGS" ["SYNTHPROV_FLAGS"]
#   One measurement step. Fresh synthprov on every provider VM (ports 8080-8083, new record files),
#   nstat snapshots before/after on every VM, synchronized olg start on every loadgen (rate split
#   evenly), providers stopped (records flushed), everything collected zstd-compressed to
#   $RUNS/RUN/<vm>/, then analyze.py into $RUNS/RUN/analysis/.
#
#   DIRECT (loadgen -> synthprov): leave TARGETS unset; analysis runs with --mode direct.
#   SUT    (loadgen -> gateway -> synthprov): set TARGETS="http://gw1:port,http://gw2:port" (the SUT
#          must already be configured with synthprov as its upstream, e.g. http://<prov-ip>:8080);
#          analysis runs with --mode sut plus $ANALYZE_FLAGS (e.g. --profile-stages ... --policy
#          enforce --corpus <local corpus path>).
#   env: LANE (required), EVID, RUNS (default $EVID/runs), TARGETS, ANALYZE_FLAGS, PROV_ENV,
#        EXTRA_VMS (more VMs whose ~/rv/runs/RUN should be collected),
#        PROXY_VM (a VM running rvproxy for this RUN, started with deploy/proxy_start.sh: it is stopped
#        -- flushing its ground-truth delay log -- before collection, and collected)
source "$(dirname "$0")/lib.sh"
run=$1 rate=$2 lgs=$3 provs=$4 olgflags=$5 provflags=${6:-}
RUNS=${RUNS:-$EVID/runs}
mode=sut
if [[ -z "${TARGETS:-}" ]]; then
  mode=direct
  TARGETS=""
  for p in $provs; do ip=$(vm_ip "$p"); for port in 8080 8081 8082 8083; do TARGETS+="http://$ip:$port,"; done; done
  TARGETS=${TARGETS%,}
fi
extra="${EXTRA_VMS:-} ${PROXY_VM:-}"
for v in $lgs $provs $extra; do rssh "$v" "mkdir -p ~/rv/runs/$run && nstat -az > ~/rv/runs/$run/nstat.before 2>/dev/null" & done; wait
for p in $provs; do "$HARNESS/deploy/prov_start.sh" "$p" "$run" $provflags & done; wait
"$HARNESS/deploy/olg_run.sh" "$run" "$rate" "$TARGETS" "$olgflags" $lgs
for p in $provs; do "$HARNESS/deploy/prov_stop.sh" "$p" "$run" & done; wait
[[ -n "${PROXY_VM:-}" ]] && "$HARNESS/deploy/proxy_stop.sh" "$PROXY_VM" "$run"
for v in $lgs $provs $extra; do rssh "$v" "nstat -az > ~/rv/runs/$run/nstat.after 2>/dev/null" & done; wait
"$HARNESS/deploy/collect.sh" "$run" "$RUNS/$run" $lgs $provs $extra
cl=(); pv=()
for l in $lgs; do cl+=("$RUNS/$run/$l/lg"); done
for p in $provs; do pv+=("$RUNS/$run/$p/prov"); done
/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python "$HARNESS/analyze.py" --client "${cl[@]}" \
  --provider "${pv[@]}" --mode "$mode" --out "$RUNS/$run/analysis" ${ANALYZE_FLAGS:-} | head -14
