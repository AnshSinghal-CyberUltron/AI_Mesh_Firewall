#!/usr/bin/env bash
# unit_ctl.sh start|stop IP PROVIDER_URL [ENV=VAL ...]  (re)start rvproto local_gpu 22M owner topology
#   Always: shared Redis RV_REDIS_URL=redis://10.146.0.13:6379/0, metrics dir on tmpfs
#   (RV_METRICS_DIR=/dev/shm/rv-metrics; proto-bench-unit found boot-disk dumps stalled the guard owner).
#   Records start output, contract, code hashes, env into $EVID/unit-starts/<ip>-<utc>/.
source "$(dirname "$0")/env.sh"
source $SP/rvproto/deploy/ssh.sh
op=$1 ip=$2; shift 2
if [[ $op == stop ]]; then rvssh $ip 'bash ~/rv/rvproto/deploy/stop_unit.sh; sleep 2; pgrep -af rvproto.serve | head -2 || echo stopped'; exit 0; fi
purl=$1; shift
out=$EVID/unit-starts/$ip-$(date -u +%H%M%S); mkdir -p $out
envs="RV_REDIS_URL=redis://10.146.0.13:6379/0 RV_PROVIDER_URL=$purl RV_METRICS_DIR=/dev/shm/rv-metrics $*"
echo "$envs" > $out/env.txt
rvssh $ip 'bash ~/rv/rvproto/deploy/stop_unit.sh; sleep 2; rm -rf /dev/shm/rv-metrics' > $out/pre-stop.txt 2>&1
rvssh $ip "cd ~/rv && $envs timeout 900 bash ~/rv/rvproto/deploy/start_unit.sh local_gpu 22M" > $out/start.txt 2>&1 || { echo "START FAILED $ip"; tail -20 $out/start.txt; exit 1; }
rvssh $ip 'curl -s localhost:8400/_rv/contract' > $out/contract.json
rvssh $ip 'cd ~/rv/rvproto && find rvproto tools deploy -type f \( -name "*.py" -o -name "*.sh" -o -name "*.txt" \) -not -path "*/__pycache__/*" | sort | xargs sha256sum | sha256sum; nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader; hostname' > $out/state.txt 2>&1
echo "$ip $(tail -1 $out/start.txt) workers=$(python3 -c "import json; print(json.load(open('$out/contract.json'))['workers'])") code=$(head -1 $out/state.txt | cut -c1-12)"
