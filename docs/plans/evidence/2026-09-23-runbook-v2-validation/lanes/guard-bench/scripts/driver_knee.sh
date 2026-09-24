#!/usr/bin/env bash
# Knee search + confirmation (GCP.md hygiene) for one in-process config and one p99 target:
#  1) fine sweep: 4 rates between LO (passed in the ladder) and HI (failed), 30 s each after 10 s warm-up;
#  2) knee = highest fine rate that passes with every lower fine rate passing (else LO); next = the following rate;
#  3) confirmation: 3 x 300 s at the knee + 1 x 300 s at the next rate (all steps run; verdicts recomputed from raw).
# usage: driver_knee.sh <model> <backend> <mix> <B> <wait_us> <LO_wps> <HI_wps> <target_ms> <tag>
set -uo pipefail
M=$1; BK=$2; MX=$3; B=$4; WT=$5; LO=$6; HI=$7; TGT=$8; TAG=$9
source ~/gb/gpuenv.sh; cd ~/gb
O=~/gb/out/A/$M/knee_$TAG; mkdir -p $O/fine $O/confirm
F="--onnx-dir $HOME/gb/onnx/$M --tok-dir $HOME/gb/models/$M --corpus $HOME/gb/corpus"
if [[ $MX == headline ]]; then WARG="--W 2 --wmix headline"; else WARG="--W $MX"; fi
MB=$B; [[ $B -lt 4 ]] && MB=4
nvidia-smi dmon -s pucvmet -d 1 -o T > $O/dmon.log 2>&1 &
DMON=$!
FINE=$(python -c "lo,hi=$LO,$HI; print(','.join(str(round(lo+(hi-lo)*k/5,1)) for k in (1,2,3,4)))")
python scripts/microbatch_bench.py $F --backend $BK --max-batch $MB $WARG --B $B --wait-us $WT --rates $FINE \
   --dur 30 --warm 10 --min-reqs 0 --no-stop --out-dir $O/fine > $O/fine.log 2>&1
read KNEE NEXT < <(python - <<PY
import sys; sys.path.insert(0, "scripts")
import analyze_ladder as AL
r = AL.analyze("$O/fine")
rates = [$LO] + [s["offered_wps"] for s in r["steps"]] + [$HI]
ok = [True] + [(s["completed"] == s["n"] and s["drops_gt5ms"] == 0 and s["lat_ms"] and s["lat_ms"]["p99"] <= $TGT) for s in r["steps"]] + [False]
k = 0
while k + 1 < len(ok) and ok[k + 1]:
    k += 1
print(rates[k], rates[k + 1])
PY
)
echo "knee=$KNEE next=$NEXT target=$TGT" > $O/knee.txt
python scripts/microbatch_bench.py $F --backend $BK --max-batch $MB $WARG --B $B --wait-us $WT \
   --rates $KNEE,$KNEE,$KNEE,$NEXT --dur 300 --warm 10 --min-reqs 0 --no-stop --out-dir $O/confirm > $O/confirm.log 2>&1
kill $DMON
echo DRIVER_KNEE_DONE $M $TAG knee=$KNEE next=$NEXT
