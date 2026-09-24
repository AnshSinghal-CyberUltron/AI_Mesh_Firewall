#!/usr/bin/env bash
# guard6_setup.sh: wait for g2_retry.sh to create rv-split-guard-6, then provision it exactly like phase-B guards 1-5
# (rvproto-frozen-1 tarball, manifest check, start_guard_node.sh 22M with AMF_TARGET_P99_MS=1000 and the metrics dump
# on /dev/shm) and write $E/.guard6-ready (owner endpoint) or $E/.guard6-none.
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; export LANE=split EVID=$E; source $SP/harness/deploy/lib.sh
L=$E/notes/g2-retry-guard6.log; g=rv-split-guard-6
until grep -q "CREATED\|GAVE UP" "$L" 2>/dev/null; do sleep 10; done
if grep -q "GAVE UP" "$L"; then echo "$(date -u +%T) no guard-6 (stock-out)" > $E/.guard6-none; exit 0; fi
for i in $(seq 1 60); do rssh $g true 2>/dev/null && break; sleep 3; done
rscp_to $g '~/' $E/rvproto-frozen-1-code.tar.gz
rssh $g 'set -e; cd ~/rv && rm -rf rvproto vendor && tar -xzf ~/rvproto-frozen-1-code.tar.gz -C ~/rv && cd ~/rv/rvproto && sed -n "/^MANIFEST$/,/^ARTIFACTS$/p" VERSION | sed "1d;\$d" | sha256sum -c --quiet && echo "$(hostname) tree == rvproto-frozen-1"; AMF_TARGET_P99_MS=1000 RV_METRICS_DIR=/dev/shm/rv-metrics timeout 900 bash ~/rv/rvproto/deploy/start_guard_node.sh 22M >/dev/null 2>&1 || true; for i in $(seq 1 300); do [ -f ~/rv/run/guard/guard-0.sock.ready ] && break; sleep 1; done; cat ~/rv/run/guard/guard-0.sock.ready; echo; nvidia-smi -L' > $E/starts/phaseB/$g.txt 2>&1
ep=$(python3 -c 'import json,sys; print(next(json.loads(l)["endpoints"][0] for l in open(sys.argv[1]) if l.startswith("{")))' $E/starts/phaseB/$g.txt 2>/dev/null)
if [[ -n $ep ]]; then echo "$ep" > $E/.guard6-ready; echo "$(date -u +%T) guard-6 ready $ep"; else echo "$(date -u +%T) guard-6 NOT ready" > $E/.guard6-none; cat $E/starts/phaseB/$g.txt | tail -5; fi
