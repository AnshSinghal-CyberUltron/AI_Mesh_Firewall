#!/usr/bin/env bash
# mk_unit.sh NAME MACHINE_TYPE [ZONES...]  -- create an rvproto unit from image family rv-proto-unit
# (SP/rvproto/deploy/create_unit.sh), trying zones in order on stock-out; ledger + zone cache.
source "$(dirname "$0")/env.sh"
set -uo pipefail
name=$1 mt=$2; shift 2
zones=("$@"); [[ ${#zones[@]} -gt 0 ]] || zones=(asia-south1-c asia-south1-a asia-south1-b)
for z in "${zones[@]}"; do
  out=$(bash $SP/rvproto/deploy/create_unit.sh "$name" "$z" pbf "$mt" 2>&1); rc=$?
  echo "$(date -u +%FT%TZ) $name $mt $z rc=$rc :: $(echo "$out" | tr '\n' ' ' | cut -c1-400)" >> $EVID/logs/mk_unit.log
  if [[ $rc == 0 ]]; then
    python3 $H/deploy/ledger.py $EVID/vm-ledger.jsonl create "$name" "$z" "$mt" pbf "rvproto unit (image rv-proto-unit)"
    echo "$z" > $EVID/.zone-$name
    ip=$(gcloud compute instances describe "$name" --zone "$z" --format='value(networkInterfaces[0].networkIP)')
    echo "$name $ip" >> $EVID/.vm-ips
    echo "CREATED $name $z $ip"; exit 0
  fi
  if grep -qE "ZONE_RESOURCE_POOL_EXHAUSTED|does not have enough resources|stockout|STOCKOUT" <<<"$out"; then
    echo "stockout $name $mt $z"; continue
  fi
  echo "FAILED $name $z: $out"; exit 1
done
echo "NOCAP $name $mt"; exit 2
