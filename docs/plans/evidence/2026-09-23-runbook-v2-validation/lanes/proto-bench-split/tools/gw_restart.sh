#!/usr/bin/env bash
# gw_restart.sh LABEL OWNER_ADDRS   restart the gateway VM's rvproto with the given owners (config only)
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export LANE=split EVID=$SP/evidence/proto-bench-split
source "$SP/harness/deploy/lib.sh"
label=$1 addrs=$2
out=$EVID/starts/$label; mkdir -p "$out"
envs="RV_GUARD_OWNER_ADDRS=$addrs RV_PROVIDER_URL=http://$(vm_ip rv-split-prov-1):8080 RV_SEED=1 RV_METRICS_DIR=/dev/shm/rv-metrics AMF_TARGET_P99_MS=1000 RV_GUARD_DEADLINE_MS=20"
echo "$envs" > "$out/env.txt"
rssh rv-split-gw-1 'bash ~/rv/rvproto/deploy/stop_unit.sh; sleep 2; ps -eo args | grep -c "[r]vproto\.serve" || true' > "$out/pre-stop.txt" 2>&1
rssh rv-split-gw-1 "$envs timeout 600 bash ~/rv/rvproto/deploy/start_gateway_node.sh 22M" > "$out/start.txt" 2>&1
rssh rv-split-gw-1 'redis-cli config set save "" >/dev/null; echo "redis save=[$(redis-cli config get save | tail -1)]"; cd ~/rv/rvproto && sed -n "/^MANIFEST$/,/^ARTIFACTS$/p" VERSION | sed "1d;\$d" | sha256sum -c --quiet && echo tree-ok' >> "$out/start.txt"
rssh rv-split-gw-1 'curl -s localhost:8400/_rv/contract' > "$out/contract.json"
rssh rv-split-gw-1 'for i in 1 2 3 4 5 6; do curl -s localhost:8400/readyz; echo; done' > "$out/readyz.txt"
tail -3 "$out/start.txt"; head -1 "$out/readyz.txt" | cut -c1-400
