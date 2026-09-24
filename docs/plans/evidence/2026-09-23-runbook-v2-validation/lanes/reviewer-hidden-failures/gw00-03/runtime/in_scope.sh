#!/usr/bin/env bash
# Runs INSIDE a transient systemd --user scope that carries CPUQuota=200% MemoryMax=2G.
# Moves itself into a child cgroup (what any delegating supervisor / nested runtime does),
# then runs the unmodified GW03 CLI against the real kernel files.
set -u
SCOPE=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
echo "scope=$SCOPE"
echo "scope cpu.max=$(cat $SCOPE/cpu.max) memory.max=$(cat $SCOPE/memory.max)"
mkdir -p "$SCOPE/gw"
echo $$ > "$SCOPE/gw/cgroup.procs"
echo "+cpu +memory" > "$SCOPE/cgroup.subtree_control" && echo "enabled cpu+memory on child"
echo "now in: $(cat /proc/self/cgroup)"
echo "child cpu.max=$(cat $SCOPE/gw/cpu.max 2>&1) memory.max=$(cat $SCOPE/gw/memory.max 2>&1)"
cd "$1" && PYTHONPATH=$PWD /home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2/.venv/bin/python -m gateway_v2.runtime | python3 -c "import sys,json; d=json.load(sys.stdin); print('GW03 CLI =>', json.dumps({k:d[k] for k in ('cpu_quota','cpu_source','memory_limit','mem_source','workers','queue_depth','pools')}))"
