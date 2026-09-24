#!/usr/bin/env bash
# chainE.sh (parallel set: rv-split-gw-4, rv-split-lg-3, rv-split-prov-3 + the guards chainD does not use yet):
#   E1 (e) 1 gateway + 1 guard (guard-6), rvproto-frozen-1 (knobs OFF, 1 s metrics dump = phase A (a)), local redis,
#      Poisson arrivals: s1p-098; if it fails s1p-078; 2 repeats at the passing one.
#   E2 li build on gw-4 + guard-6; 1 gateway + 2 guards (guard-5, guard-6), knobs ON, local redis: li12-373 (one run:
#      does the knobs-OFF gateway-loop limit of (b) at 373 move?). Then releases guard-5 to chainD ($E/.li35-go).
source "$(dirname "$0")/chainlib.sh"
PAR=("GWS=rv-split-gw-4" "PROVS=rv-split-prov-3" "LGS=rv-split-lg-3")
echo "$(date -u +%T) === E1 (e) 1gw+1g Poisson, frozen build (guard-6)"
bash "$T/gw_config.sh" e-1gw1g "rv-split-gw-4" "$G6" local "rv-split-prov-3" | tail -1
ladder_knee s1p "98" "78" "OLG_EXTRA=-sample-mod 1 -arrival poisson" LABEL=e_1gw1g_poisson_zone_b GUARDS=rv-split-guard-6 "${PAR[@]}"
echo "$(date -u +%T) === E2 li 1gw+2g (guard-5, guard-6) knobs ON at 373"
bash "$T/deploy_li.sh" rv-split-gw-4 rv-split-guard-6
bash "$T/gw_config.sh" li-1gw2g "rv-split-gw-4" "$G5,$G6" local "rv-split-prov-3" "${KNOBS[@]}" | tail -1
step li12-373 373 LABEL=li_1gw2g "GUARDS=rv-split-guard-5 rv-split-guard-6" "${PAR[@]}" "BUILD=$LIB" SAMPLER_EXTRA=--sigusr1
rssh rv-split-gw-4 'bash ~/rv/rvproto/deploy/stop_unit.sh >/dev/null 2>&1; echo gw-4 stopped'
touch "$E/.li35-go"
echo "$(date -u +%T) CHAINE DONE"
