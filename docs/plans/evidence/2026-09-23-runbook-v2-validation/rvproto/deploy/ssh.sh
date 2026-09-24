#!/usr/bin/env bash
# ssh/scp helpers for rv-* VMs (GCP.md conventions: rv key, internal IP, no gcloud compute ssh)
SP=${SP:-/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad}
SSHOPT=(-i "$SP/gcp/rv_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10)
rvssh() { local host=$1; shift; ssh "${SSHOPT[@]}" "rv@$host" "$@"; }
rvscp() { scp "${SSHOPT[@]}" "$@"; }
