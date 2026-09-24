#!/usr/bin/env bash
# chainD.sh: the <= $5k split fleet with the loop-isolation knobs ON (build: rvproto-frozen-1 + tools/apply_li_split.py,
# tree 8ace2294...; C4 rule: C4 p99 < 20 ms AND infra <= 0.1% AND 0 drops), edge + shared redis, 2 loadgens, 2 providers:
#   D1 3 gateways + 4 guards (fits the lean $4,161.69 AND the conservative $4,631.81 basis): ladder 466 -> 520 -> 582
#      (fallback 415, 373), 3/3 at the highest pass; one Poisson run at ~60% of the knee; worst-band corpus
#      (worst-22M.jsonl, 1,024 PG2 tokens in / 400 out) at ~25% and ~50% of the knee.
#   D2 add the 5th guard (3 + 5 = $4,726.46, lean basis only): ladder 582 -> 650 -> 728 (fallback 520, 466), 3/3.
#      Waits for chainE to release guard-5 ($E/.li35-go).
source "$(dirname "$0")/chainlib.sh"
COMMON=(TARGETS=http://10.160.0.11:8080 "GWS=rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" "PROVS=rv-split-prov-1 rv-split-prov-2"
        "LGS=rv-split-lg-1 rv-split-lg-2" "EXTRA_SUT=rv-split-edge-1 rv-split-redis-1" "BUILD=$LIB" SAMPLER_EXTRA=--sigusr1)
echo "$(date -u +%T) === D1 li 3gw+4g + edge + shared redis (knobs ON)"
bash "$T/gw_config.sh" li-3gw4g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" "$G1,$G2,$G3,$G4" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" "${KNOBS[@]}" | tail -3
bash "$T/edge_config.sh" li-3gw4g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" | tail -2
G4L=("GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4")
ladder_knee li34 "466 520 582" "415 373" LABEL=li_3gw4g_edge "${G4L[@]}" "${COMMON[@]}"
K=${KNEE_RESULT:-0}
if [[ $K -gt 0 ]]; then
  rp=$(python3 -c "print(round(0.6*$K))"); w1=$(python3 -c "print(round(0.25*$K))"); w2=$(python3 -c "print(round(0.5*$K))")
  step "li34p-$(printf %03d $rp)" "$rp" "OLG_EXTRA=-sample-mod 1 -arrival poisson" LABEL=li_3gw4g_edge_poisson_60pct "${G4L[@]}" "${COMMON[@]}"
  CORPUS=worst-22M.jsonl step "li34w-$(printf %03d $w1)" "$w1" LABEL=li_3gw4g_edge_worstband_25pct "${G4L[@]}" "${COMMON[@]}"
  CORPUS=worst-22M.jsonl step "li34w-$(printf %03d $w2)" "$w2" LABEL=li_3gw4g_edge_worstband_50pct "${G4L[@]}" "${COMMON[@]}"
fi
echo "$(date -u +%T) === D2 li 3gw+5g (waiting for chainE to release guard-5)"
until [[ -f $E/.li35-go ]]; do sleep 10; done
bash "$T/gw_config.sh" li-3gw5g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" "$G1,$G2,$G3,$G4,$G5" 10.160.0.12:6379 "rv-split-prov-1 rv-split-prov-2" "${KNOBS[@]}" | tail -3
bash "$T/edge_config.sh" li-3gw5g "rv-split-gw-1 rv-split-gw-2 rv-split-gw-3" | tail -2
ladder_knee li35 "582 650 728" "520 466" LABEL=li_3gw5g_edge "GUARDS=rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5" "${COMMON[@]}"
echo "$(date -u +%T) CHAIND DONE"
