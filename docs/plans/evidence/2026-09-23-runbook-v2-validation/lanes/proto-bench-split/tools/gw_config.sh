#!/usr/bin/env bash
# gw_config.sh LABEL "GW [GW...]" OWNER_ADDRS REDIS PROVS [ENV=VAL...]
#   (Re)start rvproto on each gateway VM with the given topology (configuration only; rvproto-frozen-1).
#   REDIS: "local" (each gateway's own redis, seeded by itself) or host:port of ONE shared redis
#          (seeded once, by the first gateway, before the others start).
#   PROVS: space-separated synthprov VMs; gateway i uses PROVS[i % n] as RV_PROVIDER_URL (port 8080).
#   RV_GUARD_FLEET_WORKERS = 12 x number of gateways (each worker's share of the owners' capacity).
#   Always: AMF_TARGET_P99_MS=1000 RV_GUARD_DEADLINE_MS=20 RV_METRICS_DIR=/dev/shm/rv-metrics (unit lane p22u config).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export LANE=split EVID=$SP/evidence/proto-bench-split
source "$SP/harness/deploy/lib.sh"
label=$1 gws=$2 addrs=$3 redis=$4 provs=$5; shift 5
extra="$*"
out=$EVID/starts/$label; mkdir -p "$out"
read -r -a G <<<"$gws"; read -r -a P <<<"$provs"
nfleet=$((12 * ${#G[@]}))
for g in "${G[@]}"; do rssh "$g" 'bash ~/rv/rvproto/deploy/stop_unit.sh >/dev/null 2>&1; sleep 1; true' & done; wait
i=0
for g in "${G[@]}"; do
  pv=${P[$((i % ${#P[@]}))]}
  if [[ $redis == local ]]; then rurl=redis://127.0.0.1:6379/0; seed="RV_SEED=1"; else rurl=redis://$redis/0; seed=""; [[ $i == 0 ]] && seed="RV_SEED=1"; fi
  envs="RV_GUARD_OWNER_ADDRS=$addrs RV_PROVIDER_URL=http://$(vm_ip "$pv"):8080 RV_REDIS_URL=$rurl $seed RV_GUARD_FLEET_WORKERS=$nfleet RV_METRICS_DIR=/dev/shm/rv-metrics AMF_TARGET_P99_MS=1000 RV_GUARD_DEADLINE_MS=20 $extra"
  echo "$g $envs" >> "$out/env.txt"
  cmd="$envs timeout 600 bash ~/rv/rvproto/deploy/start_gateway_node.sh 22M"
  if [[ ${NODUMP:-0} == 1 ]]; then
    # metrics dump disabled: start_gateway_node.sh defaults an empty RV_METRICS_DIR to ~/rv/metrics, so run the
    # same launcher with the script's exports but RV_METRICS_DIR empty (configuration only; same code)
    cmd="rm -f /dev/shm/rv-metrics/worker-*.json; ulimit -n \$(ulimit -Hn); cd ~/rv/rvproto && \
      if [[ -n '$seed' ]]; then ~/rv/venv/bin/python tools/seed.py $rurl --flush >/dev/null; fi; env $envs RV_METRICS_DIR= \
      RV_GATEWAY_V2_PATH=\$HOME/rv/vendor RV_GUARD_TOPOLOGY=remote RV_GUARD_TOKENIZER=\$HOME/rv/models/tokenizer-22M.json \
      RV_GUARD_MODEL_SHA256=\$(cut -d' ' -f1 \$HOME/rv/models/pg2-22M.onnx.sha256) RV_GUARD_SEQ_BUCKETS=512 \
      setsid nohup \$HOME/rv/venv/bin/python -m rvproto.serve > \$HOME/rv/logs/rvproto-gateway.log 2>&1 < /dev/null & echo \$! > \$HOME/rv/run/rvproto.pid; \
      for k in \$(seq 1 120); do curl -sf -m 2 localhost:8400/readyz >/dev/null && break; sleep 1; done; sleep 5; echo NODUMP-READY"
  fi
  if [[ $i == 0 || $redis == local ]]; then
    rssh "$g" "$cmd" > "$out/start-$g.txt" 2>&1
  else
    rssh "$g" "$cmd" > "$out/start-$g.txt" 2>&1 &
  fi
  i=$((i + 1))
done
wait
for g in "${G[@]}"; do
  rssh "$g" 'redis-cli config set save "" >/dev/null 2>&1; cd ~/rv/rvproto && sed -n "/^MANIFEST$/,/^ARTIFACTS$/p" VERSION | sed "1d;\$d" | sha256sum -c --quiet && echo tree-ok; curl -s localhost:8400/_rv/contract' > "$out/contract-$g.json.txt" 2>&1
  echo "$g: $(tail -1 "$out/start-$g.txt") | $(rssh "$g" 'curl -s localhost:8400/readyz' | cut -c1-260)"
done
