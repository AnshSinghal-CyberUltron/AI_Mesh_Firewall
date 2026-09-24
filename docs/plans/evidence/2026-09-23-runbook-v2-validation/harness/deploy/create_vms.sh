#!/usr/bin/env bash
# create_vms.sh MACHINE_TYPE NAME [NAME...]
#   Creates CPU VMs per GCP.md (labels, ephemeral ssh key, block-project-ssh-keys, Ubuntu 24.04),
#   falls back asia-south1-c -> -a -> -b on stock-out, records the ledger, waits for ssh, runs
#   prep_host.sh and pushes bin/. Names must be rv-<LANE>-<role>-<n>.
#   env: LANE (required), EVID, ZONE, NOTE, DISK_GB (default 50)
source "$(dirname "$0")/lib.sh"
mtype=$1; shift
disk_type=pd-balanced
case "$mtype" in c4-*|c4d-*|c3-*|c3d-*|n4-*) disk_type=hyperdisk-balanced ;; esac
create_one() {
  local name=$1 z out
  [[ "$name" == rv-"$LANE"-* ]] || { log "refusing: $name is not rv-$LANE-*"; return 1; }
  for z in "$ZONE" asia-south1-a asia-south1-b; do
    if out=$(gcloud compute instances create "$name" --project "$PROJECT" --zone "$z" --machine-type "$mtype" \
        --image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64 \
        --boot-disk-type="$disk_type" --boot-disk-size="${DISK_GB:-50}GB" \
        --labels="purpose=runbook-validation,lane=$LANE,owner=claude" \
        --metadata-from-file=ssh-keys="$SP/gcp/ssh-keys.txt" --metadata=block-project-ssh-keys=TRUE \
        --format='value(networkInterfaces[0].networkIP)' 2>&1); then
      ledger create "$name" "$z" "$mtype" "$LANE" "${NOTE:-harness}"
      local ip; ip=$(echo "$out" | tail -1)
      echo "$name $ip" >> "$IPCACHE"
      log "created $name ($mtype) in $z ip=$ip"
      echo "$z" > "$EVID/.zone-$name"
      return 0
    fi
    if grep -q -E "ZONE_RESOURCE_POOL_EXHAUSTED|does not have enough resources|stockout" <<<"$out"; then
      log "$name: stock-out in $z, trying next zone"; continue
    fi
    log "create $name failed: $out"; return 1
  done
  log "$name: no capacity in asia-south1-{c,a,b}"; return 1
}
wait_ssh() {
  local name=$1 i
  for i in $(seq 1 60); do
    if rssh "$name" true 2>/dev/null; then return 0; fi
    sleep 5
  done
  log "ssh to $name never came up"; return 1
}
pids=()
for n in "$@"; do create_one "$n" & pids+=($!); done
rc=0; for p in "${pids[@]}"; do wait "$p" || rc=1; done
[[ $rc == 0 ]] || exit 1
for n in "$@"; do
  (wait_ssh "$n" && rscp_to "$n" /tmp/prep_host.sh "$HARNESS/deploy/prep_host.sh" && rssh "$n" bash /tmp/prep_host.sh \
     && rscp_to "$n" rv/bin/ "$HARNESS"/bin/synthprov "$HARNESS"/bin/olg "$HARNESS"/bin/rvproxy "$HARNESS"/bin/SHA256SUMS \
     && rssh "$n" 'cd ~/rv/bin && sha256sum -c --quiet SHA256SUMS && echo "binaries ok on $(hostname)"') &
done
wait
