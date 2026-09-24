#!/usr/bin/env bash
# deploy_li.sh VM [VM...]: stop rvproto on each VM, replace ~/rv/{rvproto,vendor,models/tokenizer} with the li-knobs build
# (rvproto-li-code.tar.gz = rvproto-frozen-1 + tools/apply_li_split.py) and verify VERSION.li's manifest there.
# Guard VMs (name contains "guard") are restarted at once with the owner dump knob:
#   RV_METRICS_DUMP_S=0 AMF_TARGET_P99_MS=1000 RV_METRICS_DIR=/dev/shm/rv-metrics start_guard_node.sh 22M
# Gateways are started later by gw_config.sh with the gateway knobs.
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; export LANE=split EVID=$E; source $SP/harness/deploy/lib.sh; set +e
mkdir -p $E/starts/li
VER='cd ~/rv/rvproto && sed -n "/^MANIFEST$/,/^END$/p" VERSION.li | sed "1d;\$d" | sha256sum -c --quiet && echo "$(hostname -s) tree == rvproto-frozen-1+li-knobs ($(sed -n "/^MANIFEST$/,/^END$/p" VERSION.li | sed "1d;\$d" | sha256sum | cut -c1-16))" || echo "$(hostname -s) LI TREE MISMATCH"'
one() {
  local v=$1
  rscp_to $v '~/' $E/rvproto-li-code.tar.gz || { echo "$v scp failed"; return 1; }
  if [[ $v == *guard* ]]; then
    rssh $v "bash ~/rv/rvproto/deploy/stop_unit.sh >/dev/null 2>&1; sleep 2; pkill -f '[r]vproto.serve' 2>/dev/null; sleep 1; \
      cd ~/rv && rm -rf rvproto vendor && tar -xzf ~/rvproto-li-code.tar.gz -C ~/rv && $VER; rm -f ~/rv/run/guard/guard-*.sock.ready; \
      RV_METRICS_DUMP_S=0 AMF_TARGET_P99_MS=1000 RV_METRICS_DIR=/dev/shm/rv-metrics timeout 900 bash ~/rv/rvproto/deploy/start_guard_node.sh 22M >/dev/null 2>&1 || true; \
      for i in \$(seq 1 300); do [ -f ~/rv/run/guard/guard-0.sock.ready ] && break; sleep 1; done; cat ~/rv/run/guard/guard-0.sock.ready; echo; \
      tr '\0' '\n' < /proc/\$(pgrep -f '[r]vproto.serve --guard-owner 0' | head -1)/environ | grep -E '^RV_METRICS_DUMP_S=|^AMF_TARGET_P99_MS='" > $E/starts/li/$v.txt 2>&1
  else
    rssh $v "bash ~/rv/rvproto/deploy/stop_unit.sh >/dev/null 2>&1; sleep 2; cd ~/rv && rm -rf rvproto vendor && tar -xzf ~/rvproto-li-code.tar.gz -C ~/rv && $VER" > $E/starts/li/$v.txt 2>&1
  fi
  echo "$v: $(head -1 $E/starts/li/$v.txt) | $(tail -2 $E/starts/li/$v.txt | tr '\n' ' ' | cut -c1-260)"
}
for v in "$@"; do one $v & done; wait
