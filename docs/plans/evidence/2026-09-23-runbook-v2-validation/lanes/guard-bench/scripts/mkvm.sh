#!/usr/bin/env bash
# usage: mkvm.sh <name> <zone> <machine_type> <gpu|cpu> [disk_gb]
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
EV=$SP/evidence/guard-bench
name=$1; zone=$2; mt=$3; kind=$4; disk=${5:-}
common=(--project ai-mesh-firewall --zone "$zone" --machine-type "$mt"
  --labels=purpose=runbook-validation,lane=guard,owner=claude
  --metadata-from-file=ssh-keys=$SP/gcp/ssh-keys.txt --metadata=block-project-ssh-keys=TRUE)
if [[ $kind == gpu ]]; then
  gcloud compute instances create "$name" "${common[@]}" \
    --image-project=deeplearning-platform-release --image-family=common-cu129-ubuntu-2204-nvidia-580 \
    --maintenance-policy=TERMINATE --boot-disk-size=${disk:-200}GB --boot-disk-type=pd-balanced
else
  gcloud compute instances create "$name" "${common[@]}" \
    --image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 \
    --boot-disk-size=${disk:-60}GB --boot-disk-type=hyperdisk-balanced
fi
python3 $EV/scripts/ledger.py create "$name" "$zone" "$mt" "$kind"
