#!/usr/bin/env bash
# ssh.sh <ip> <cmd...>
ip=$1; shift
exec ssh -i /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/gcp/rv_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=30 rv@$ip "$@"
