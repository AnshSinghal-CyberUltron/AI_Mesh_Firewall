#!/usr/bin/env bash
# hostinfo.sh <outfile-prefix>: record host facts required by GCP.md measurement hygiene.
p=${1:-hostinfo}
{
  echo "## date"; date -u +%Y-%m-%dT%H:%M:%SZ
  echo "## hostname"; hostname
  echo "## uname -a"; uname -a
  echo "## nproc"; nproc
  echo "## lscpu"; lscpu
  echo "## free -g"; free -g
  echo "## os-release"; cat /etc/os-release
  echo "## cgroup"; stat -fc %T /sys/fs/cgroup
  echo "## metadata machine-type/zone"
  curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/machine-type; echo
  curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/zone; echo
  curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/image; echo
  if command -v nvidia-smi >/dev/null; then echo "## nvidia-smi -q"; nvidia-smi -q; fi
  if command -v docker >/dev/null; then echo "## docker version"; sudo docker version; sudo docker compose version; fi
} > "${p}.txt" 2>&1
echo "wrote ${p}.txt"
