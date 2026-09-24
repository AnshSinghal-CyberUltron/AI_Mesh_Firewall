#!/usr/bin/env bash
# GCP.md measurement hygiene: record host facts next to every result. Usage: hostinfo.sh <outdir>
OUT=${1:-$HOME/rv/hostinfo}; mkdir -p "$OUT"
lscpu > "$OUT/lscpu.txt"; nproc > "$OUT/nproc.txt"; free -g > "$OUT/free.txt"; uname -a > "$OUT/uname.txt"
cat /etc/os-release > "$OUT/os-release.txt"; ulimit -Hn > "$OUT/nofile-hard.txt"
command -v nvidia-smi >/dev/null && nvidia-smi -q > "$OUT/nvidia-smi-q.txt"
[ -x "$HOME/rv/venv/bin/python" ] && "$HOME/.local/bin/uv" pip freeze -p "$HOME/rv/venv/bin/python" > "$OUT/pip-freeze.txt" 2>/dev/null
curl -s -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: d.get(k) for k in ("name","machineType","zone","image","cpuPlatform")}, indent=1))' \
  > "$OUT/gce-instance.json" 2>/dev/null || true
command -v docker >/dev/null && sudo docker images --digests > "$OUT/docker-images.txt" 2>/dev/null
echo "$OUT"
