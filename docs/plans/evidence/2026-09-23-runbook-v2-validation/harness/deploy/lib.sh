# Sourced by the deploy/*.sh helpers. Follows SP/GCP.md conventions.
# Required env: LANE (e.g. harness, micro, proto). Optional: EVID (evidence dir holding vm-ledger.jsonl),
# ZONE (default asia-south1-c), MACHINE_IMAGE_FAMILY.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
HARNESS="$SP/harness"
PROJECT=ai-mesh-firewall
: "${LANE:?set LANE=<lane> (vm names rv-<lane>-<role>-<n>, label lane=<lane>)}"
EVID="${EVID:-$SP/evidence/harness-builder}"
ZONE="${ZONE:-asia-south1-c}"
KEY="$SP/gcp/rv_ed25519"
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
          -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=10)
mkdir -p "$EVID"
LEDGER="$EVID/vm-ledger.jsonl"
IPCACHE="$EVID/.vm-ips"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

# vm_zone NAME -> zone of an existing instance (searches the region's zones)
vm_zone() {
  gcloud compute instances list --project "$PROJECT" --filter="name=($1)" --format='value(zone.basename())' 2>/dev/null | head -1
}

# vm_ip NAME -> internal IP (cached)
vm_ip() {
  local name=$1 ip
  if [[ -f "$IPCACHE" ]] && ip=$(awk -v n="$name" '$1==n{print $2}' "$IPCACHE") && [[ -n "$ip" ]]; then
    echo "$ip"; return
  fi
  ip=$(gcloud compute instances list --project "$PROJECT" --filter="name=($name)" \
        --format='value(networkInterfaces[0].networkIP)' 2>/dev/null | head -1)
  [[ -n "$ip" ]] || { log "no such VM: $name"; return 1; }
  echo "$name $ip" >> "$IPCACHE"
  echo "$ip"
}

rssh() { local name=$1; shift; ssh "${SSH_OPTS[@]}" "rv@$(vm_ip "$name")" "$@"; }
rscp_to() { local name=$1 dst=$2; shift 2; scp -q "${SSH_OPTS[@]}" "$@" "rv@$(vm_ip "$name"):$dst"; }
rscp_from() { local name=$1 src=$2 dst=$3; scp -q -r "${SSH_OPTS[@]}" "rv@$(vm_ip "$name"):$src" "$dst"; }

ledger() { python3 "$HARNESS/deploy/ledger.py" "$LEDGER" "$@"; }
