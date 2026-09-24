#!/usr/bin/env bash
# Records host facts required by GCP.md measurement hygiene.
mkdir -p ~/gb/hostinfo; cd ~/gb/hostinfo
lscpu > lscpu.txt; nproc > nproc.txt; free -g > free.txt; uname -a > uname.txt; cat /etc/os-release > os-release.txt
md() { curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/$1"; }
{ echo "image=$(md image)"; echo "machine_type=$(md machine-type)"; echo "zone=$(md zone)"; echo "name=$(md name)"; echo "cpu_platform=$(md cpu-platform)"; } > gce.txt
if command -v nvidia-smi >/dev/null; then nvidia-smi -q > nvidia-smi-q.txt; nvidia-smi > nvidia-smi.txt; fi
[ -x ~/venv/bin/python ] && ~/.local/bin/uv pip freeze -p ~/venv/bin/python > pip-freeze.txt 2>/dev/null
date -u +%FT%TZ > captured_utc.txt
