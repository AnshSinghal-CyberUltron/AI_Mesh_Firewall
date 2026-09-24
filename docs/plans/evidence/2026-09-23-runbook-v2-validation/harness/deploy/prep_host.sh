#!/usr/bin/env bash
# Runs ON a fresh VM (as user rv): kernel/network limits for tens of thousands of concurrent
# streams, zstd for compressed collection, and the host facts GCP.md requires (lscpu, nproc,
# free -g, uname -a, image) saved to ~/rv/hostinfo.txt.
set -euo pipefail
sudo sysctl -q -w net.core.somaxconn=65535 net.ipv4.tcp_max_syn_backlog=65535 \
  net.ipv4.ip_local_port_range="1024 65535" net.ipv4.tcp_tw_reuse=1 net.core.netdev_max_backlog=250000 \
  fs.file-max=4194304 fs.nr_open=4194304 net.ipv4.tcp_fin_timeout=15 \
  net.core.rmem_max=16777216 net.core.wmem_max=16777216
if [[ -e /proc/sys/net/netfilter/nf_conntrack_max ]]; then
  sudo sysctl -q -w net.netfilter.nf_conntrack_max=4194304 || true
fi
printf 'rv soft nofile 1048576\nrv hard nofile 1048576\nrv - rtprio 99\n' | sudo tee /etc/security/limits.d/90-rv.conf >/dev/null
# Scheduler latency for the instrument: EEVDF's default base slice (2.8 ms on 8 vCPUs) plus
# RUN_TO_PARITY let a woken thread wait ~one slice for a busy CPU; traces showed 2-4 ms dispatcher
# stalls (runs/diag-6000). Lower the slice and disable run-to-parity on harness VMs (recorded below).
sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null || true
if [[ -w /sys/kernel/debug/sched/base_slice_ns ]] || sudo test -e /sys/kernel/debug/sched/base_slice_ns; then
  echo 300000 | sudo tee /sys/kernel/debug/sched/base_slice_ns >/dev/null
  echo NO_RUN_TO_PARITY | sudo tee /sys/kernel/debug/sched/features >/dev/null
fi
if ! command -v zstd >/dev/null; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null && sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install -y zstd >/dev/null
fi
mkdir -p ~/rv/bin ~/rv/corpora ~/rv/runs
{
  echo "## date -u"; date -u
  echo "## uname -a"; uname -a
  echo "## nproc"; nproc
  echo "## free -g"; free -g
  echo "## lscpu"; lscpu
  echo "## os-release"; cat /etc/os-release
  echo "## image"; curl -s -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/image || true; echo
  echo "## machine-type"; curl -s -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/machine-type || true; echo
  echo "## zone"; curl -s -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/instance/zone || true; echo
  echo "## sysctl"; sysctl net.core.somaxconn net.ipv4.ip_local_port_range net.ipv4.tcp_tw_reuse fs.nr_open
  echo "## conntrack"; lsmod | grep -c conntrack || true
  echo "## sched"; echo "base_slice_ns=$(sudo cat /sys/kernel/debug/sched/base_slice_ns 2>/dev/null)"; sudo cat /sys/kernel/debug/sched/features 2>/dev/null
  echo "## nic"; ip -br link; ethtool -i ens4 2>/dev/null | head -2 || true
} > ~/rv/hostinfo.txt 2>&1
echo "prep ok: $(hostname) $(nproc) vCPU"
