#!/usr/bin/env bash
# chainE1.sh: replaces chainE.sh after the controller's final list (skip E2 li12-373 and the 3gw+5g knobs-ON step).
# (e) 1 gateway + 1 guard (guard-6), rvproto-frozen-1 (knobs OFF), local redis, Poisson: s1p-098 (already started by
# chainE.sh, which was stopped with its step left running), then per the ladder: 2 repeats at 98 if it passed, else 78 (+2).
source "$(dirname "$0")/chainlib.sh"
PAR=("GWS=rv-split-gw-4" "PROVS=rv-split-prov-3" "LGS=rv-split-lg-3")
while pgrep -f "[s]plit_step.sh s1p-098 " >/dev/null; do sleep 5; done
echo "$(date -u +%T) s1p-098 step finished: $(v s1p-098)"
ladder_knee s1p "98" "78" "OLG_EXTRA=-sample-mod 1 -arrival poisson" LABEL=e_1gw1g_poisson_zone_b GUARDS=rv-split-guard-6 "${PAR[@]}"
echo "$(date -u +%T) CHAINE1 DONE"
