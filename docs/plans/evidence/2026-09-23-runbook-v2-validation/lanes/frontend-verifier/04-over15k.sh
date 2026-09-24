#!/usr/bin/env bash
# Every tracked frontend source file (.js/.jsx/.css, excluding *.test.js and non-source dirs) > 15,000 bytes, at a ref.
cd /home/contact_cyberultron_com/AI_Mesh_Firewall
ref=${1:-HEAD}
git ls-tree -r -l "$ref" -- frontend/src | awk '{print $4, $5}' \
 | grep -E '\.(jsx|js|css|ts|tsx)$' | grep -v '\.test\.js$' | grep -v '/\.claude-flow/' \
 | awk '$1>15000 {printf "%8d  %6.1f KiB  %s\n", $1, $1/1024, $2}' | sort -rn
