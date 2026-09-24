#!/usr/bin/env bash
# delete_vms.sh NAME [NAME...]   delete rv-<LANE>-* VMs and close their ledger entries
source "$(dirname "$0")/lib.sh"
for n in "$@"; do
  [[ "$n" == rv-* ]] || { log "refusing to delete non rv-* VM $n"; exit 1; }
  (z=$(cat "$EVID/.zone-$n" 2>/dev/null || vm_zone "$n")
   if gcloud compute instances delete "$n" --zone "$z" --project "$PROJECT" --quiet >/dev/null 2>&1; then
     ledger delete "$n"; log "deleted $n ($z)"
   else log "delete $n failed (already gone?)"; fi) &
done
wait
[[ -f "$IPCACHE" ]] && for n in "$@"; do sed -i "/^$n /d" "$IPCACHE"; done
gcloud compute instances list --project "$PROJECT" --filter="labels.lane=$LANE" --format='table(name,zone.basename(),status)'
