#!/usr/bin/env bash
# recreate_main.sh -- re-create the main-lane VMs of this lane with their previous internal IPs
# (--private-network-ip) so every IP-keyed script and iptables label stays valid. GCP.md labels/ssh.
source "$(dirname "$0")/env.sh"
Z=asia-northeast1-a
COMMON=(--project ai-mesh-firewall --zone $Z --labels=purpose=runbook-validation,lane=pbf,owner=claude
        --metadata-from-file=ssh-keys=$SP/gcp/ssh-keys.txt --metadata=block-project-ssh-keys=TRUE)
mk() { # name type ip kind
  local name=$1 mt=$2 ip=$3 kind=$4 out
  if [[ $kind == g2 ]]; then
    out=$(gcloud compute instances create $name "${COMMON[@]}" --machine-type $mt --image-project ai-mesh-firewall \
      --image rv-proto-unit-img-20260923f --maintenance-policy=TERMINATE --boot-disk-size=200GB --boot-disk-type=pd-balanced \
      --private-network-ip=$ip 2>&1)
  else
    out=$(gcloud compute instances create $name "${COMMON[@]}" --machine-type $mt --image-project=ubuntu-os-cloud \
      --image-family=ubuntu-2404-lts-amd64 --boot-disk-type=hyperdisk-balanced --boot-disk-size=50GB --private-network-ip=$ip 2>&1)
  fi
  if [[ $? == 0 ]]; then
    python3 $H/deploy/ledger.py $EVID/vm-ledger.jsonl create $name $Z $mt pbf "re-created for unbounded C4-rule scaling (same IP $ip)"
    echo "$Z" > $EVID/.zone-$name; echo "CREATED $name $ip"
  else
    echo "FAILED $name: $(echo "$out" | tail -2 | tr '\n' ' ' | cut -c1-300)"
  fi
}
mk rv-pbf-unit-2 g2-standard-24 10.146.0.2 g2 & mk rv-pbf-unit-3 g2-standard-24 10.146.0.5 g2 &
mk rv-pbf-unit-4 g2-standard-24 10.146.0.3 g2 & mk rv-pbf-unit-5 g2-standard-24 10.146.0.4 g2 &
mk rv-pbf-edge-1 c4-highcpu-16 10.146.0.10 cpu & mk rv-pbf-redis-1 c4-standard-8 10.146.0.13 cpu &
mk rv-pbf-lg-1 c4-highcpu-8 10.146.0.11 cpu & mk rv-pbf-lg-2 c4-highcpu-8 10.146.0.12 cpu & mk rv-pbf-lg-3 c4-highcpu-8 10.146.0.9 cpu &
mk rv-pbf-prov-1 c4-highcpu-8 10.146.0.8 cpu & mk rv-pbf-prov-2 c4-highcpu-8 10.146.0.6 cpu &
wait
