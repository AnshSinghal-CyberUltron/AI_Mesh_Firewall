#!/usr/bin/env bash
# Exact byte sizes of the files named in rb.md §9.1 row 4, at each ref.
cd /home/contact_cyberultron_com/AI_Mesh_Firewall
for f in frontend/src/pages/AIMeshFirewallOverview.jsx frontend/src/components/AttackSimulatorPanel.jsx frontend/src/components/layout/Header.jsx frontend/src/components/layout/Sidebar.jsx; do
  for r in e95f974d 2a657fad HEAD; do
    b=$(git cat-file -s "$r:$f" 2>/dev/null || echo MISSING)
    printf "%-10s %-55s %8s bytes\n" "$r" "$f" "$b"
  done
  printf "%-10s %-55s %8s bytes\n" WORKTREE "$f" "$(stat -c %s $f)"
done
