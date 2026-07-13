#!/bin/sh
# Bootstrap DNS for gVisor sandboxes on user-defined Docker bridge networks.
# Docker's embedded resolver (127.0.0.11) is unreachable inside runsc; MCP stdio
# servers that npx-fetch packages need external nameservers in /etc/resolv.conf.
set -e
if [ "$(id -u)" = 0 ] && [ -n "${MCP_SANDBOX_DNS:-}" ]; then
  : > /etc/resolv.conf
  for ns in $(printf '%s' "$MCP_SANDBOX_DNS" | tr ',' ' '); do
    [ -n "$ns" ] || continue
    printf 'nameserver %s\n' "$ns" >> /etc/resolv.conf
  done
fi
if [ "$(id -u)" = 0 ]; then
  exec setpriv --reuid=sandbox --regid=sandbox --clear-groups -- "$@"
fi
exec "$@"
