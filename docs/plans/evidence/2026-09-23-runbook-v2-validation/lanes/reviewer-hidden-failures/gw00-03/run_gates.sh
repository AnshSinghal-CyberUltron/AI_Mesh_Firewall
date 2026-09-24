#!/usr/bin/env bash
# Replays the CI structural-gates job commands (gateway-v2.yml:24-37) against a tree.
# usage: run_gates.sh <gateway_v2 dir>
VB=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2/.venv/bin
cd "$1" || exit 99
export PYTHONPATH="$PWD"
rc_all=0
for g in check_sizes check_http_outside_edge_resolve check_no_module_mutable check_capacity_literals; do
  out=$($VB/python -m lint.$g gateway_v2 2>&1); rc=$?
  echo "gate=$g rc=$rc ${out:+| $out}"
  [ $rc -ne 0 ] && rc_all=1
done
out=$($VB/lint-imports 2>&1); rc=$?
echo "gate=import-linter rc=$rc | $(echo "$out" | grep -E 'Contracts:|KEPT|BROKEN|kept|broken' | tr '\n' ' ')"
[ $rc -ne 0 ] && rc_all=1
exit $rc_all
