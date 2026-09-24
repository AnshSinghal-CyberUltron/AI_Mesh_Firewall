#!/usr/bin/env bash
# unit_patch.sh apply|restore   swap the 4 files of the RV_EXP_OUTPUT_SCAN experiment flag into / out of
#   unit-1's rvproto tree (only while rvproto is stopped or about to be restarted; running processes
#   keep the code they imported). Verifies sha256 of every file after copying.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
X=$SP/evidence/proto-bench-unit/rvproto-exp-flag
KEY=$SP/gcp/rv_ed25519
O=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
FILES=(rvproto/runtime/config.py rvproto/edge/inspect.py rvproto/edge/state.py rvproto/edge/respond.py)
case "$1" in
  apply) src=$X/unit-patched ;;
  restore) src=$X/unit-orig ;;
  *) echo "usage: $0 apply|restore"; exit 2 ;;
esac
for f in "${FILES[@]}"; do scp -q "${O[@]}" "$src/$f" "rv@10.160.0.46:rv/rvproto/$f"; done
want=$(cd "$src" && sha256sum "${FILES[@]}")
got=$(ssh "${O[@]}" rv@10.160.0.46 "cd ~/rv/rvproto && sha256sum ${FILES[*]}")
if [[ "$want" == "$got" ]]; then echo "unit_patch $1: OK"; echo "$got"; else echo "unit_patch $1: MISMATCH"; echo "$got"; exit 1; fi
