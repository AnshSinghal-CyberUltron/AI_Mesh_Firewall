#!/usr/bin/env bash
# v1-bench VM lifecycle helper. Every create/delete is appended to vm-ledger.jsonl.
# usage: vm.sh create <name> <zone> <machine-type> [gpu|cpu]
#        vm.sh delete <name> <zone>
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
LEDGER=$E/vm-ledger.jsonl
PROJECT=ai-mesh-firewall
cmd=$1; name=$2; zone=$3
case "$cmd" in
create)
  mt=$4; kind=${5:-cpu}
  if [ "$kind" = gpu ]; then
    img=(--image-project=deeplearning-platform-release --image-family=common-cu129-ubuntu-2204-nvidia-580
         --maintenance-policy=TERMINATE --boot-disk-size=200GB --boot-disk-type=pd-balanced)
  else
    case "$mt" in c4-*|c4d-*|c3-*) dt=hyperdisk-balanced;; *) dt=pd-balanced;; esac
    img=(--image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 --boot-disk-size=50GB --boot-disk-type=$dt)
  fi
  gcloud compute instances create "$name" --project $PROJECT --zone "$zone" --machine-type "$mt" "${img[@]}" \
    --labels=purpose=runbook-validation,lane=v1,owner=claude \
    --scopes=cloud-platform \
    --metadata-from-file=ssh-keys=$SP/gcp/ssh-keys.txt --metadata=block-project-ssh-keys=TRUE \
    --format="value(name,networkInterfaces[0].networkIP,status)"
  python3 $SP/harness/deploy/ledger.py "$LEDGER" create "$name" "$zone" "$mt" v1 "${NOTE:-}"
  ;;
delete)
  gcloud compute instances delete "$name" --project $PROJECT --zone "$zone" --quiet
  python3 $SP/harness/deploy/ledger.py "$LEDGER" delete "$name"
  ;;
esac
