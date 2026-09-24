#!/usr/bin/env bash
# Mirror of LGW03-5 (tests/runtime/test_lgw03_live_docker.py:86) in the DOWNSCALE direction,
# using a real kernel CPU quota (systemd --user transient scope) instead of `docker update`.
set -u
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2/.venv/bin/python
cd "$1"
UNIT=amfrev-sighup-$$
SNAP=$(mktemp)
systemd-run --user --scope --quiet --unit=$UNIT -p CPUQuota=200% -- \
  env PYTHONPATH=$PWD AMF_HOLD_LEASE=1 AMF_SNAPSHOT_PATH=$SNAP $PY -m gateway_v2.runtime --serve >/dev/null 2>$SNAP.err &
LAUNCH=$!
for i in $(seq 1 50); do [ -s $SNAP ] && break; sleep 0.1; done
GW=$(pgrep -f "gateway_v2.runtime --serve" -n)
echo "gateway pid=$GW unit=$UNIT"
python3 -c "import json;d=json.load(open('$SNAP'));print('before HUP:', {k:d[k] for k in ('cpu_quota','workers','in_flight','reload_count','pool_sizes')})"
systemctl --user set-property --runtime $UNIT.scope CPUQuota=100%     # operator shrinks the box (docker update --cpus=1 / VPA)
echo "scope cpu.max now: $(cat /sys/fs/cgroup$(cut -d: -f3 /proc/$GW/cgroup)/cpu.max)"
kill -HUP $GW
wait $LAUNCH; rc=$?
echo "gateway exit code after SIGHUP: $rc (process alive? $(kill -0 $GW 2>/dev/null && echo yes || echo no))"
echo "stderr tail:"; tail -4 $SNAP.err
rm -f $SNAP $SNAP.err
