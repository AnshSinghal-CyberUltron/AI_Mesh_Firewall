#!/bin/bash
# Claim 2(b) driver (runs on the controller; executes the benchmark on rv-micro-app-1 against rv-micro-redis-1).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/micro-claims; source $E/vms.env; D=$E/claim2/b; R=$D/raw; mkdir -p $R
RSSH=$E/bin/rssh; RSCP=$E/bin/rscp
$RSCP -r $D/emit_bench.py $D/v1shared $D/replay_payloads_P2t2_DEBUG.jsonl rv@$APP1:~/
URL=redis://$REDIS1:6379/0
bench() { # label n mode
  $RSSH $APP1 "taskset -c 2 ~/venv/bin/python ~/emit_bench.py --redis $URL --payloads ~/replay_payloads_P2t2_DEBUG.jsonl --n $2 --label $1 --mode $3 --out ~/emit_$1.jsonl" | tee -a $D/summary.txt
  $RSCP rv@$APP1:~/emit_$1.jsonl $R/
}
netem() { # delay-ms (0 = remove)
  $RSSH $REDIS1 "sudo tc qdisc del dev ens3 root 2>/dev/null || true"
  if [ "$1" != "0" ]; then $RSSH $REDIS1 "sudo tc qdisc add dev ens3 root netem delay ${1}ms limit 100000"; fi
  $RSSH $REDIS1 "tc qdisc show dev ens3" | tee -a $D/summary.txt
}
rtt() { # label
  $RSSH $APP1 "ping -q -c 500 -i 0.01 $REDIS1 | tail -2" | sed "s/^/[$1 icmp app->redis] /" | tee -a $D/summary.txt
  $RSSH $APP1 "timeout 12 redis-cli -h $REDIS1 --latency -i 10 2>&1 | tail -1" | sed "s/^/[$1 redis-cli --latency ms] /" | tee -a $D/summary.txt || true
}
subs() { # start|stop N
  if [ "$1" = start ]; then $RSSH $REDIS1 "for i in \$(seq 1 $2); do nohup redis-cli -h 127.0.0.1 subscribe logs:stream >/dev/null 2>&1 & done; sleep 1; redis-cli PUBSUB NUMSUB logs:stream | tr '\n' ' '" | sed 's/^/[subscribers] /' | tee -a $D/summary.txt; echo | tee -a $D/summary.txt
  else $RSSH $REDIS1 "pkill -f 'redis-cli -h 127.0.0.1 [s]ubscribe' || true; sleep 1; redis-cli PUBSUB NUMSUB logs:stream | tr '\n' ' '" | sed 's/^/[subscribers] /' | tee -a $D/summary.txt; echo | tee -a $D/summary.txt; fi
}
echo "=== claim2b run $(date -u +%FT%TZ) app=$APP1 redis=$REDIS1 (same zone asia-south1-c)" | tee -a $D/summary.txt
$RSSH $REDIS1 "redis-cli INFO server | grep -E 'redis_version|tcp_port'; redis-cli CONFIG GET appendonly | tr '\n' ' '" | tee -a $D/summary.txt; echo | tee -a $D/summary.txt
netem 0
bench cpu_only_noop 20000 noop
rtt natural
bench natural_0sub 20000 publish
subs start 16
bench natural_16sub 20000 publish
subs stop 0
for d in 1 5 20; do
  netem $d; rtt netem${d}ms
  n=$([ $d = 1 ] && echo 5000 || ([ $d = 5 ] && echo 2000 || echo 500))
  bench netem${d}ms_0sub $n publish
done
netem 0
rtt natural_after
echo "=== done $(date -u +%FT%TZ)" | tee -a $D/summary.txt
