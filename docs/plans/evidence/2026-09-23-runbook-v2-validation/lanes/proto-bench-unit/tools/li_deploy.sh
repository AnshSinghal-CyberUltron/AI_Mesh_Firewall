#!/usr/bin/env bash
# li_deploy.sh apply|restore   swap the loop-isolation-knob files (tools/apply_li.py) into / out of unit-1's
#   rvproto tree (while rvproto is stopped or about to be restarted). Every file's sha256 is verified.
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
RAW=/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-unit
KEY=$SP/gcp/rv_ed25519
O=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
FILES=(rvproto/runtime/config.py rvproto/edge/app.py rvproto/detect/guard/owner.py rvproto/detect/semantic.py
       rvproto/edge/chat.py rvproto/edge/state.py rvproto/egress/stream.py rvproto/edge/respond.py)
case "$1" in apply) src=$RAW/li-tree ;; restore) src=$RAW/li-orig ;; *) echo "usage: $0 apply|restore"; exit 2 ;; esac
for f in "${FILES[@]}"; do scp -q "${O[@]}" "$src/$f" "rv@10.160.0.46:rv/rvproto/$f"; done
want=$(cd "$src" && sha256sum "${FILES[@]}")
got=$(ssh "${O[@]}" rv@10.160.0.46 "cd ~/rv/rvproto && sha256sum ${FILES[*]}")
if [[ "$want" == "$got" ]]; then echo "li_deploy $1: OK"; echo "$got"; else echo "li_deploy $1: MISMATCH"; echo "$got"; exit 1; fi
