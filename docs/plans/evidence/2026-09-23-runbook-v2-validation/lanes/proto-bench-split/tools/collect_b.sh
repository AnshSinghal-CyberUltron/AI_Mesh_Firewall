#!/usr/bin/env bash
# collect_b.sh: after the last phase-B run, record host facts (rvproto deploy/hostinfo.sh on gateways and guards; a
# minimal equivalent on the edge and redis VMs, plus the live nginx config / redis version) and verify the
# rvproto-frozen-1 manifest AFTER the runs on every gateway and guard. Output: $E/hostinfo-b/<vm>/, $E/notes/frozen-verify-after-b.txt
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; export LANE=split EVID=$E; source $SP/harness/deploy/lib.sh; set +e
H=$E/hostinfo-b; mkdir -p $H; V=$E/notes/frozen-verify-after-b.txt; touch $V
# usage: collect_b.sh [VM ...]  (default: every phase B/C VM). Appends to $V.
ALL="rv-split-gw-1 rv-split-gw-2 rv-split-gw-3 rv-split-gw-4 rv-split-guard-1 rv-split-guard-2 rv-split-guard-3 rv-split-guard-4 rv-split-guard-5 rv-split-guard-6 rv-split-edge-1 rv-split-redis-1"
VMS="${*:-$ALL}"
VER='cd ~/rv/rvproto && if [ -f VERSION.li ]; then sed -n "/^MANIFEST$/,/^END$/p" VERSION.li | sed "1d;\$d" | sha256sum -c --quiet && echo "AFTER $(date -u +%TZ): $(hostname -s) tree == rvproto-frozen-1+li-knobs ($(sed -n "/^MANIFEST$/,/^END$/p" VERSION.li | sed "1d;\$d" | sha256sum | cut -c1-16))" || echo "AFTER: $(hostname -s) LI TREE MISMATCH"; else sed -n "/^MANIFEST$/,/^ARTIFACTS$/p" VERSION | sed "1d;\$d" | sha256sum -c --quiet && echo "AFTER $(date -u +%TZ): $(hostname -s) tree == rvproto-frozen-1 ($(sed -n "/^MANIFEST$/,/^ARTIFACTS$/p" VERSION | sed "1d;\$d" | sha256sum | cut -c1-16))" || echo "AFTER: $(hostname -s) TREE MISMATCH"; fi'
for vm in $VMS; do
  [[ $vm == *gw-* || $vm == *guard-* ]] || continue
  ( rssh $vm "bash ~/rv/rvproto/deploy/hostinfo.sh ~/rv/hostinfo-b >/dev/null 2>&1; cp ~/rv/logs/prebuild_trt.json ~/rv/hostinfo-b/ 2>/dev/null; \
      (sha256sum ~/rv/trtcache/* 2>/dev/null | cut -c1-64,65-200) > ~/rv/hostinfo-b/engine-sha256.txt; $VER" >> $V 2>&1
    rm -rf $H/$vm; rscp_from $vm '~/rv/hostinfo-b' $H/$vm ) &
done
for vm in $VMS; do
  [[ $vm == *edge-* || $vm == *redis-* ]] || continue
  ( rssh $vm 'o=~/hostinfo-b; mkdir -p $o; lscpu > $o/lscpu.txt; nproc > $o/nproc.txt; free -g > $o/free.txt; uname -a > $o/uname.txt; \
      cat /etc/os-release > $o/os-release.txt; (nginx -V 2>&1; sudo cat /etc/nginx/nginx.conf 2>/dev/null) > $o/nginx.txt 2>&1; \
      (redis-server --version; redis-cli info server | head -20) > $o/redis.txt 2>&1; \
      curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k: d.get(k) for k in (\"name\",\"machineType\",\"zone\",\"image\",\"cpuPlatform\")}, indent=1))" > $o/gce-instance.json' 2>/dev/null
    rm -rf $H/$vm; rscp_from $vm '~/hostinfo-b' $H/$vm ) &
done
wait
( cd $SP/rvproto && sed -n '/^MANIFEST$/,/^ARTIFACTS$/p' VERSION | sed '1d;$d' | sha256sum -c --quiet && echo "AFTER: controller SP/rvproto tree == rvproto-frozen-1" || echo "AFTER: controller SP/rvproto TREE MISMATCH" ) >> $V 2>&1
sort $V; for vm in $(ls $H); do printf "%s: %s | %s\n" $vm "$(python3 -c "import json; d=json.load(open('$H/$vm/gce-instance.json')); print(d['machineType'].split('/')[-1], d['zone'].split('/')[-1], d.get('cpuPlatform'))" 2>/dev/null)" "$(grep -m1 'Model name' $H/$vm/lscpu.txt 2>/dev/null | sed 's/  */ /g')"; done
