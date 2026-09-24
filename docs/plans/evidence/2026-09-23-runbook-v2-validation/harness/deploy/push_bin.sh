#!/usr/bin/env bash
# push_bin.sh NAME [NAME...]   (re)deploy bin/* to VMs and verify checksums
source "$(dirname "$0")/lib.sh"
for n in "$@"; do rscp_to "$n" rv/bin/ "$HARNESS"/bin/synthprov "$HARNESS"/bin/olg "$HARNESS"/bin/rvproxy "$HARNESS"/bin/SHA256SUMS & done
wait
for n in "$@"; do rssh "$n" "cd ~/rv/bin && sha256sum -c --quiet SHA256SUMS && echo \"bin ok \$(hostname -s) \$(./olg -h 2>&1 | head -0)\""; done
