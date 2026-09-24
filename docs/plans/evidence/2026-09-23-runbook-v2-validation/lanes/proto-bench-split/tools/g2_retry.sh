#!/usr/bin/env bash
# g2_retry.sh NAME ZONE TRIES INTERVAL_S: retry creating one g2-standard-4 guard VM (same flags as phase B) until it succeeds
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; n=$1 z=$2 tries=${3:-15} iv=${4:-60}
for i in $(seq 1 "$tries"); do
  out=$(gcloud compute instances create "$n" --project ai-mesh-firewall --zone "$z" --machine-type g2-standard-4 --image-project ai-mesh-firewall \
    --image-family rv-proto-unit --maintenance-policy=TERMINATE --boot-disk-size=200GB --boot-disk-type=pd-balanced \
    --labels=purpose=runbook-validation,lane=split,owner=claude --metadata-from-file=ssh-keys=$SP/gcp/ssh-keys.txt \
    --metadata=block-project-ssh-keys=TRUE --format='value(networkInterfaces[0].networkIP)' 2>&1); rc=$?
  echo "$(date -u +%T) $n $z g2-standard-4 rc=$rc $(echo "$out" | tail -1 | grep -o 'STOCKOUT\|ZONE_RESOURCE_POOL_EXHAUSTED\|[0-9.]*$' | head -1)" | tee -a "$E/notes/g2-zone-attempts.txt"
  if [[ $rc == 0 ]]; then
    python3 "$SP/harness/deploy/ledger.py" "$E/vm-ledger.jsonl" create "$n" "$z" g2-standard-4 split "phase B guard-6 for the resized <=\$5k fleet (2gw+6g)"
    echo "$z" > "$E/.zone-$n"; echo "CREATED $n $(echo "$out" | tail -1)"; exit 0
  fi
  sleep "$iv"
done
echo "GAVE UP $n $z after $tries tries"; exit 1
