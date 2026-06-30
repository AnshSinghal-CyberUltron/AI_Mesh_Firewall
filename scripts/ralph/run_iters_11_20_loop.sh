#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/../.."
RESULTS="scripts/ralph/regression_iters_9_20_results.tsv"
printf 'iter\tresult\tseconds\n9\tPASS\t940\n10\tPASS\t1511\n' > "$RESULTS"
for i in $(seq 11 20); do
  echo "=== START ITER $i $(date -u +%FT%TZ) ==="
  start=$(date +%s)
  if ./scripts/ralph/run_regression_iteration.sh "$i"; then
    res=PASS
  else
    res=FAIL
    dur=$(( $(date +%s) - start ))
    printf '%s\t%s\t%s\n' "$i" "$res" "$dur" >> "$RESULTS"
    echo "STOPPED_AT=$i"
    exit 1
  fi
  dur=$(( $(date +%s) - start ))
  printf '%s\t%s\t%s\n' "$i" "$res" "$dur" >> "$RESULTS"
done
echo "ALL_PASS $(date -u +%FT%TZ)"
