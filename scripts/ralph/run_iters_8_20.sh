#!/usr/bin/env bash
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
RESULTS="$REPO_ROOT/scripts/ralph/regression_iters_8_20_results.tsv"
echo -e "iter\tstatus\tseconds\tended" > "$RESULTS"
for i in $(seq 8 20); do
  start=$(date +%s)
  if ./scripts/ralph/run_regression_iteration.sh "$i"; then
    st=PASS
    rc=0
  else
    st=FAIL
    rc=1
  fi
  end=$(date +%s)
  echo -e "$i\t$st\t$((end-start))\t$(date -u +%FT%TZ)" | tee -a "$RESULTS"
  if (( rc != 0 )); then
    echo "STOPPED_AT_ITER=$i" >> "$RESULTS"
    exit 1
  fi
done
echo "ALL_DONE=1" >> "$RESULTS"
