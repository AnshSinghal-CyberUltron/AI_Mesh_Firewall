#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
RESULTS="$ROOT/scripts/ralph/regression_iters_9_20_results.tsv"
printf 'iter\tresult\tseconds\n' > "$RESULTS"
for i in $(seq 9 20); do
  echo "=== START ITER $i $(date -u +%FT%TZ) ==="
  start=$(date +%s)
  if ./scripts/ralph/run_regression_iteration.sh "$i"; then
    res=PASS
  else
    res=FAIL
    dur=$(( $(date +%s) - start ))
    printf '%s\t%s\t%s\n' "$i" "$res" "$dur" >> "$RESULTS"
    echo "STOPPED_AT=$i $(date -u +%FT%TZ)"
    exit 1
  fi
  dur=$(( $(date +%s) - start ))
  printf '%s\t%s\t%s\n' "$i" "$res" "$dur" >> "$RESULTS"
done
echo "ALL_PASS $(date -u +%FT%TZ)"
