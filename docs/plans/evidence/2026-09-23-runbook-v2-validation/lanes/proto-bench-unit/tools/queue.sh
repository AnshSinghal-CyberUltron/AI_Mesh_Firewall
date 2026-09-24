#!/usr/bin/env bash
# queue.sh QUEUE_FILE   run queued steps sequentially, following the file as it grows.
#   Step line:    RUN RATE CORPUS [ENV=VAL ...]   (shell quoting honoured, e.g. PROV_FLAGS="-ttft 150ms -itl 10ms")
#   Command line: CMD <shell command>             (e.g. restart the unit with another variant)
#   '#' lines are skipped; "STOP" ends the queue; at end of file the queue waits for more lines.
#   Steps already present in RUNS are skipped. Analysis runs in the background (ASYNC=1).
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
T=$SP/evidence/proto-bench-unit/tools
RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit/runs}
LOGS=$(dirname "$RUNS")/logs
mkdir -p "$LOGS"
q=$1
n=0
while true; do
  total=$(wc -l < "$q")
  if [[ $n -ge $total ]]; then sleep 15; continue; fi
  n=$((n + 1))
  line=$(sed -n "${n}p" "$q")
  [[ -z "${line// }" || "$line" == \#* ]] && continue
  [[ "$line" == STOP* ]] && break
  if [[ "$line" == CMD\ * ]]; then
    echo "$(date -u +%T) CMD ${line#CMD }"
    eval "${line#CMD }" > "$LOGS/cmd-$n.log" 2>&1
    echo "$(date -u +%T) CMD rc=$? (log $LOGS/cmd-$n.log)"
    continue
  fi
  read -r run rate corpus rest <<<"$line"
  if [[ -d "$RUNS/$run" ]]; then echo "$(date -u +%T) skip $run (exists)"; continue; fi
  echo "$(date -u +%T) START $run rate=$rate corpus=$corpus env=[$rest]"
  eval "env ASYNC=1 $rest bash \"$T/pbu_step.sh\" \"$run\" \"$rate\" \"$corpus\"" > "$LOGS/$run.log" 2>&1
  echo "$(date -u +%T) END $run rc=$?"
done
echo "$(date -u +%T) QUEUE DONE"
