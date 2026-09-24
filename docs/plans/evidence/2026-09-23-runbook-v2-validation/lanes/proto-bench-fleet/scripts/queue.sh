#!/usr/bin/env bash
# queue.sh QUEUEFILE LOG  -- run steps line by line: "RUN RATE [VAR=VAL ...]"; blank/# lines skipped.
# Each step runs via run_step.sh (frozen copy). Appends the step's headline to LOG.
S=$(cd "$(dirname "$0")" && pwd)
q=$1 log=$2
while read -r run rate rest; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  echo "$(date -u +%T) START $run $rate $rest" >> "$log"
  eval "env $rest bash \"\$S/run_step.sh\" \"\$run\" \"\$rate\"" > "$S/../logs/$run.log" 2>&1 < /dev/null
  echo "$(date -u +%T) DONE $run: $(grep -m1 '^# ' "$S/../logs/$run.log") | $(grep -m1 '^offered' "$S/../logs/$run.log")" >> "$log"
done < "$q"
echo "$(date -u +%T) QUEUE_END $q" >> "$log"
