#!/usr/bin/env bash
# pyspy_all.sh OUTDIR SECONDS   (runs ON the SUT) — py-spy record (raw collapsed stacks, 100 Hz,
# non-blocking, idle frames excluded) on every gunicorn worker of the gateway container in parallel.
set -euo pipefail
out=$1; secs=$2; mkdir -p "$out"
CG=/sys/fs/cgroup/system.slice/docker-$(sudo docker inspect -f '{{.Id}}' aimeshperf-gateway-1).scope
master=$(sudo docker inspect -f '{{.State.Pid}}' aimeshperf-gateway-1)
for p in $(cat $CG/cgroup.procs); do
  grep -q gunicorn /proc/$p/cmdline 2>/dev/null || continue
  [ "$p" = "$master" ] && continue
  sudo /usr/local/bin/py-spy record --pid $p --duration $secs --rate 100 --nonblocking --format raw \
     --output "$out/worker-$p.txt" > "$out/worker-$p.log" 2>&1 &
done
wait
sudo chown -R "$USER" "$out"
ls "$out"/*.txt | wc -l
