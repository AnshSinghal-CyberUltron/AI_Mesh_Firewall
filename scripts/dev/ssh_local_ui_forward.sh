#!/usr/bin/env bash
# Run on your LAPTOP (not the VM) to make localhost:8180 hit this remote stack.
# Usage:
#   ./scripts/dev/ssh_local_ui_forward.sh [user@host]
# Example:
#   ./scripts/dev/ssh_local_ui_forward.sh contact_cyberultron_com@8.231.115.48
set -euo pipefail
HOST="${1:-contact_cyberultron_com@8.231.115.48}"
echo "Forwarding local 8180/8770/8100/8300 → ${HOST} (IPv4 + IPv6 localhost)…"
echo "Then open: http://127.0.0.1:8180/login  or  http://localhost:8180/login"
exec ssh \
  -L 127.0.0.1:8180:127.0.0.1:8180 \
  -L "[::1]:8180:127.0.0.1:8180" \
  -L 127.0.0.1:8770:127.0.0.1:8770 \
  -L "[::1]:8770:127.0.0.1:8770" \
  -L 127.0.0.1:8100:127.0.0.1:8100 \
  -L 127.0.0.1:8300:127.0.0.1:8300 \
  -N "$HOST"
