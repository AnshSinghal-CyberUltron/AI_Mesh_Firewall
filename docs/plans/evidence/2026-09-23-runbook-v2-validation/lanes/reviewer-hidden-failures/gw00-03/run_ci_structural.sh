#!/usr/bin/env bash
# Full replay of CI job `structural-gates` (.github/workflows/gateway-v2.yml:10-37)
# against a copied tree, with `bash -e` semantics per step reported individually.
VB=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway_v2/.venv/bin
cd "$1" || exit 99
export PYTHONPATH="$PWD"
fail=0
step() { local name="$1"; shift; out=$("$@" 2>&1); rc=$?; echo "step[$name] rc=$rc :: $(echo "$out" | tail -n ${TAILN:-2} | tr '\n' ' ' | cut -c1-400)"; [ $rc -ne 0 ] && fail=1; }
step sizes     $VB/python -m lint.check_sizes gateway_v2
step http      $VB/python -m lint.check_http_outside_edge_resolve gateway_v2
step mutable   $VB/python -m lint.check_no_module_mutable gateway_v2
step capacity  $VB/python -m lint.check_capacity_literals gateway_v2
step imports   $VB/lint-imports
step ruff      $VB/ruff check --no-cache gateway_v2 lint tests
step mypy      $VB/mypy --strict --cache-dir=/dev/null
step pytest    $VB/python -m pytest -q -p no:cacheprovider
echo "JOB structural-gates => $([ $fail -eq 0 ] && echo GREEN || echo RED)"
exit $fail
