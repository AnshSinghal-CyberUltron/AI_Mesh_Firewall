#!/usr/bin/env bash
# profile_later.sh DELAY_S DURATION_S   (runs ON the unit) after DELAY_S, py-spy record --nonblocking (raw collapsed
# stacks, 100 Hz) two workers and guard owner 0 for DURATION_S, into /dev/shm/pyspy/. Returns immediately.
delay=$1 dur=$2
mkdir -p /dev/shm/pyspy
for w in $(pgrep -f "rvproto.serve --worker" | head -2) $(pgrep -f "rvproto.serve --guard-owner 0"); do
  nohup bash -c "sleep $delay; sudo /home/rv/pyspy/bin/py-spy record --nonblocking -f raw -r 100 -d $dur -p $w -o /dev/shm/pyspy/prof-$w.txt" \
    > /dev/shm/pyspy/log-$w.txt 2>&1 < /dev/null &
done
echo "scheduled in ${delay}s for ${dur}s: $(pgrep -f 'rvproto.serve --worker' | head -2 | tr '\n' ' ') owner0 $(pgrep -f 'rvproto.serve --guard-owner 0')"
