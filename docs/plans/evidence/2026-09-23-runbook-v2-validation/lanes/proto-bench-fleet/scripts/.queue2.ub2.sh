#!/usr/bin/env bash
# queue2.sh QUEUEFILE LOG -- like queue.sh, plus lines "EDGE TAG ip:port,ip:port" (edge upstream switch)
# and "CMD shell..." (arbitrary command). Run a COPY of this file (never edit a running script).
S=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/proto-bench-fleet/scripts
q=$1 log=$2
while read -r run rate rest; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  if [[ $run == EDGE ]]; then
    echo "$(date -u +%T) EDGE $rate $(bash $S/edge_set.sh $rate "${rest//,/ }" 2>&1 < /dev/null | tr '\n' ' ')" >> "$log"; continue
  fi
  if [[ $run == CMD ]]; then
    echo "$(date -u +%T) CMD $rate $rest: $(eval "$rate $rest" 2>&1 < /dev/null | tail -3 | tr '\n' ' ')" >> "$log"; continue
  fi
  echo "$(date -u +%T) START $run $rate $rest" >> "$log"
  eval "env $rest bash \"\$S/run_step.sh\" \"\$run\" \"\$rate\"" > "$S/../logs/$run.log" 2>&1 < /dev/null
  echo "$(date -u +%T) DONE $run: $(grep -m1 '^# ' "$S/../logs/$run.log") | $(grep -m1 '^offered' "$S/../logs/$run.log")" >> "$log"
done < "$q"
echo "$(date -u +%T) QUEUE_END $q" >> "$log"
