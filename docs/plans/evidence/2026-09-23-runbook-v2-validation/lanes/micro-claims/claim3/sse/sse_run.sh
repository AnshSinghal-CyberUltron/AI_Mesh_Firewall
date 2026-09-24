#!/bin/bash
# sse_run.sh LABEL CONFIG TARGET [EXTRA_OLG_ARGS]   (rv-micro-lg-5 -> [rv-micro-edge-2 nginx CONFIG] -> synthprov on rv-micro-up-2:8300)
# synthprov: TTFT 150 ms, ITL 20 ms, 100 tokens, 1 token per SSE chunk, flush per chunk; ALL requests sampled (per-chunk timing).
# olg: 10 streams/s open-loop, ramp 2 s + warm-up 10 s excluded, 120 s measured, per-chunk arrival timing for all requests.
set -u
source /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/micro-claims/bin/lib.sh
LABEL=$1 CONFIG=$2 TARGET=$3 EXTRA=${4:-}
D=$E/claim3/sse/runs/$LABEL; mkdir -p $D
$RSSH $UP2 "./up.sh synth $LABEL" < /dev/null > $D/up_start.txt
if [ $CONFIG = direct ]; then $RSSH $EDGE2 "./edge.sh stop" < /dev/null > $D/edge_start.txt; else $RSSH $EDGE2 "./edge.sh start $CONFIG" < /dev/null > $D/edge_start.txt; fi
START=$(( $(now_ms) + 4000 ))
olg_run $LG5 "-targets $TARGET -corpus ~/corpus_sse.jsonl -sse-frac 1 -rate 10 -duration 120 -warmup 10 -ramp 2 -sample-mod 1 -H X-Forwarded-Proto:https $EXTRA" $LABEL $START 120 > $D/olg_done_line.txt
$RSSH $UP2 "./up.sh stop" < /dev/null > /dev/null; sleep 3
olg_fetch $LG5 $LABEL $D $RAWROOT/claim3_sse/$LABEL
$RSCP rv@$UP2:synth_records_$LABEL.jsonl $D/synth_records.jsonl
[ $CONFIG != direct ] && $RSSH $EDGE2 "sudo docker exec edge sh -c 'cat /etc/nginx/conf.d/01-http.conf' | grep -n 'proxy_buffering' | head -8" < /dev/null > $D/edge_buffering_directives.txt
python3 $E/claim3/sse/sse_analyze.py $RAWROOT/claim3_sse/$LABEL/requests.jsonl.zst $D/synth_records.jsonl > $D/sse_summary.json 2> $D/sse_analyze.err || true
echo "$LABEL $(cat $D/sse_summary.json)"
