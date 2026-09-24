#!/usr/bin/env bash
# Exp C (run on the CONTROLLER): off-box client (c4-standard-8, same zone) -> Triton on a G2 over the VPC.
# For every batcher config: fresh tritonserver on the G2 + config pushed locally there, then the ladder runs on the c4.
# usage: driver_c.sh <server-ip> <model> <triton-name> <cap_wps> <Ws> <"MB:Q[:I] ..."> <net 0|1>
set -uo pipefail
EV=$(cd "$(dirname "$0")/.." && pwd); source $EV/scripts/hosts.env
S=$1; M=$2; TN=$3; CAP=$4; WS=$5; CFGS=$6; NET=${7:-1}
R="$EV/scripts/r"; TAG=26.05; PY='~/venv/bin/python'
T="--model $TN --tok-dir \$HOME/gb/models/$M --corpus \$HOME/gb/corpus"
O="~/gb/out/C/$M"
RATES=$(python3 -c "c=$CAP; print(','.join(str(round(c*f,1)) for f in (0.05,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.1)))")
srv() {  # $1 = queue_us (-1 = no dynamic batching) $2 = max_batch $3 = instances
  timeout 300 $R $S "bash ~/gb/scripts/triton_up.sh $TAG >/dev/null; cd ~/gb && $PY scripts/triton_bench.py load --url localhost:8001 $T --queue-us $1 --max-batch $2 --instances $3 > /dev/null 2>&1; echo srv-ok" </dev/null
}
timeout 60 $R $C41 "mkdir -p $O" </dev/null
if [[ $NET == 1 ]]; then
  timeout 30 $R $S 'pgrep -x netserver >/dev/null || netserver -p 12865; ps -eo args | grep -q "^sockperf server" || setsid -f sockperf server --tcp -p 11111 >/dev/null 2>&1 </dev/null' </dev/null
  timeout 600 $R $C41 "bash ~/gb/scripts/net_rtt.sh $S $O/net" </dev/null
fi
srv -1 64 1
timeout 1800 $R $C41 "cd ~/gb && $PY scripts/triton_bench.py latency --url $S:8001 $T --ws 1,2,3,4,7 --iters 3000 --warmup 300 --out $O/lat_rtt.json > $O/lat_rtt.log 2>&1" </dev/null
for W in ${WS//,/ }; do
  srv -1 64 1
  timeout 3600 $R $C41 "cd ~/gb && $PY scripts/triton_bench.py ladder --no-load --url $S:8001 $T --W $W --queue-us -1 --max-batch 64 --instances 1 --rates $RATES --dur 10 --warm 2 --stop-p99-ms 60 --out-dir $O/W${W}_nobatch > $O/W${W}_nobatch.log 2>&1" </dev/null
  for C in $CFGS; do
    MB=${C%%:*}; REST=${C#*:}; Q=${REST%%:*}; I=1; [[ $REST == *:* ]] && I=${REST#*:}
    srv $Q $MB $I
    timeout 3600 $R $C41 "cd ~/gb && $PY scripts/triton_bench.py ladder --no-load --url $S:8001 $T --W $W --queue-us $Q --max-batch $MB --instances $I --rates $RATES --dur 10 --warm 2 --stop-p99-ms 60 --out-dir $O/W${W}_mb${MB}_q${Q}_i${I} > $O/W${W}_mb${MB}_q${Q}_i${I}.log 2>&1" </dev/null
  done
done
echo DRIVER_C_DONE $M
