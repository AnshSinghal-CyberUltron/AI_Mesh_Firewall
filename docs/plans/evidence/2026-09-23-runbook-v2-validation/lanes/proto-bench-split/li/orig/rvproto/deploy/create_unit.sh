#!/usr/bin/env bash
# Create an rvproto serving unit from the prebuilt image (fast path; everything preinstalled:
# venv, ORT-GPU/TRT, Triton 26.05 image, PG2 22M/86M TensorRT caches + Triton plans for sm89/L4).
# Usage: create_unit.sh <name> [zone] [lane] [machine-type]
# Then:  ssh rv@<ip> 'RV_PROVIDER_URL=http://<synthprov>:8080 RV_SEED=1 bash ~/rv/rvproto/deploy/start_unit.sh triton_grpc 86M'
# Slow path (no image): create from the DL image family per GCP.md, scp rvproto-unit.tar.gz and
# unit_setup.sh, run `bash unit_setup.sh ~/rvproto-unit.tar.gz` (~15 min incl. Triton pull + plan build).
set -euo pipefail
SP=${SP:-/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad}
NAME=${1:?name}; ZONE=${2:-asia-south1-c}; LANE=${3:-proto}; MT=${4:-g2-standard-24}
gcloud compute instances create "$NAME" --project ai-mesh-firewall --zone "$ZONE" --machine-type "$MT" \
  --image-project ai-mesh-firewall --image-family rv-proto-unit --maintenance-policy=TERMINATE \
  --boot-disk-size=200GB --boot-disk-type=pd-balanced \
  --labels="purpose=runbook-validation,lane=$LANE,owner=claude" \
  --metadata-from-file=ssh-keys="$SP/gcp/ssh-keys.txt" --metadata=block-project-ssh-keys=TRUE
