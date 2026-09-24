#!/usr/bin/env bash
# unit_ctl.sh start LABEL BACKEND SIZE [ENV=VAL ...]   restart rvproto on unit-1 with the given variant
# unit_ctl.sh stop LABEL
#   Always: RV_PROVIDER_URL=http://<rv-pbu-prov-1>:8080, RV_SEED=1 (fresh keys/plans/budgets), metrics
#   dir on tmpfs (RV_METRICS_DIR=/dev/shm/rv-metrics; see README: file dumps on the boot disk stalled
#   the guard owner's event loop). Records start output, /_rv/contract, rvproto code hashes, the
#   effective env and GPU state into EVID/unit-starts/<LABEL>/.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
EVID=${EVID:-$SP/evidence/proto-bench-unit}
KEY=$SP/gcp/rv_ed25519
UNIT_IP=10.160.0.46
PROV_IP=${PROV_IP:-10.160.15.233}
ssh_u() { ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "rv@$UNIT_IP" "$@"; }
op=$1 label=$2; shift 2
out=$EVID/unit-starts/$label
mkdir -p "$out"
if [[ $op == stop ]]; then
  ssh_u 'bash ~/rv/rvproto/deploy/stop_unit.sh; sleep 2; pgrep -af rvproto.serve || echo "rvproto stopped"; nvidia-smi --query-gpu=index,memory.used --format=csv' | tee "$out/stop.txt"
  exit 0
fi
backend=$1 size=$2; shift 2
envs="RV_PROVIDER_URL=http://$PROV_IP:8080 RV_SEED=1 RV_METRICS_DIR=/dev/shm/rv-metrics $*"
echo "$envs" > "$out/env.txt"
ssh_u 'bash ~/rv/rvproto/deploy/stop_unit.sh; sleep 3; pgrep -af rvproto.serve && echo STILL_RUNNING || true' > "$out/pre-stop.txt" 2>&1
t0=$(date +%s)
ssh_u "$envs bash ~/rv/rvproto/deploy/start_unit.sh $backend $size" > "$out/start.txt" 2>&1 || { echo "START FAILED"; tail -30 "$out/start.txt"; exit 1; }
echo "start_seconds=$(( $(date +%s) - t0 ))" >> "$out/start.txt"
# unit_setup.sh intended Redis without RDB snapshots (sed of a commented line had no effect: built-in
# save rules forked BGSAVE every ~100 s under audit XADD load); restore the intent at runtime
ssh_u 'redis-cli config set save "" >/dev/null; echo "redis save=[$(redis-cli config get save | tail -1)]"' >> "$out/start.txt"
ssh_u 'curl -s localhost:8400/_rv/contract' > "$out/contract.json"
ssh_u 'cd ~/rv/rvproto && find rvproto tools deploy -type f \( -name "*.py" -o -name "*.sh" -o -name "*.txt" \) | sort | xargs sha256sum' > "$out/code-sha256.txt"
ssh_u 'nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu,clocks.sm,persistence_mode --format=csv; pgrep -af "rvproto.serve|tritonserver" | cut -c1-160; df -h /dev/shm | tail -1' > "$out/state.txt" 2>&1
tail -3 "$out/start.txt"
python3 -c "import json; d=json.load(open('$out/contract.json')); print('workers', d['workers'], 'guard_pool', d['pools'].get('guard'), 'bounds', d['rvproto_bounds'])"
