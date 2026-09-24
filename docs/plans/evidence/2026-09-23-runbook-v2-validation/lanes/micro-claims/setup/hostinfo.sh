#!/bin/bash
echo "### date -u"; date -u
echo "### hostname"; hostname
echo "### uname -a"; uname -a
echo "### nproc"; nproc
echo "### lscpu"; lscpu
echo "### lscpu -e"; lscpu -e
echo "### free -g"; free -g
echo "### image"; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/image'; echo
echo "### machine-type"; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/machine-type'; echo
echo "### zone"; curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/zone'; echo
echo "### os-release"; cat /etc/os-release | head -4
echo "### sysctl"; sysctl net.ipv4.ip_local_port_range net.ipv4.tcp_tw_reuse net.core.somaxconn net.ipv4.tcp_fin_timeout 2>/dev/null
echo "### cpufreq/governor"; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo n/a
if [ -x ~/venv/bin/python ]; then echo "### python"; ~/venv/bin/python -VV; echo "### pip freeze"; VIRTUAL_ENV=~/venv ~/.local/bin/uv pip freeze; fi
if command -v docker >/dev/null; then echo "### docker"; docker --version; sudo docker images --digests 2>/dev/null; fi
