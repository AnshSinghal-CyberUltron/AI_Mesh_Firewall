#!/usr/bin/env bash
# collect.sh RUN DEST NAME [NAME...]
#   zstd-compresses every *.jsonl in ~/rv/runs/RUN on each VM (keeping small json/log files as-is),
#   copies the run dir to DEST/NAME/, then deletes the remote copy. analyze.py reads *.jsonl.zst directly.
source "$(dirname "$0")/lib.sh"
run=$1 dest=$2; shift 2
mkdir -p "$dest"
for n in "$@"; do
  (rssh "$n" "cd ~/rv/runs/$run && find . -name '*.jsonl' -size +0 -exec zstd -q -T0 -3 --rm {} \; ; du -sh . | cut -f1" \
     | sed "s/^/[$n] compressed size /" >&2
   mkdir -p "$dest/$n" && rscp_from "$n" "~/rv/runs/$run/." "$dest/$n/" && rssh "$n" "rm -rf ~/rv/runs/$run") &
done
wait
log "collected $run from $* into $dest"
