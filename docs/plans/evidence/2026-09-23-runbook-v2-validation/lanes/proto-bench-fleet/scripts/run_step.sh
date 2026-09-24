#!/usr/bin/env bash
# run_step.sh RUN RATE  -- execute a frozen private copy of pbf_step.sh/pbf_finish.sh/env.sh for this run
# (bash reads scripts incrementally; editing the originals must never affect a running step)
S=$(cd "$(dirname "$0")" && pwd)
d=$(mktemp -d "$S/../.frozen.XXXXXX")
cp "$S/pbf_step.sh" "$S/pbf_finish.sh" "$S/env.sh" "$d/"
exec bash "$d/pbf_step.sh" "$@"
