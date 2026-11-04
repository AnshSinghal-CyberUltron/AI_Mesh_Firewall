#!/usr/bin/env bash
# GW29 gate commands. Image gw29-dev = python:3.12-slim + dev/conformance/serve deps + pytest-xdist
# (built from /tmp/gw29img/Dockerfile; no editable install needed: pytest pythonpath=["."]).
WT=${WT:-/work/suraj/AI_Mesh_Firewall/.worktrees/gw29}
IMG=${IMG:-gw29-dev}
run() { docker run --rm -e AMF_EDGE_DOCKER=0 -v /work/suraj/AI_Mesh_Firewall/.git:/work/suraj/AI_Mesh_Firewall/.git:ro -v "$WT":/work -w /work/gateway_v2 "$IMG" sh -c "$1"; }
case "$1" in
  g1)  run "pytest -n 24 -q -p no:cacheprovider 2>&1 | tail -15" ;;
  g1b) run "pytest -n 8 -q -p no:cacheprovider -k lgw29 2>&1 | tail -15" ;;
  g2)  run "mypy --strict 2>&1 | tail -5; ruff check . 2>&1 | tail -5; lint-imports 2>&1 | tail -8" ;;
  g3)  for g in check_capacity_literals check_no_module_mutable check_frozen_dataclasses \
               check_http_outside_edge_resolve check_sizes check_tenant_scale check_dispatch_provenance; do
         for t in gateway_v2 state_control; do run "python -m lint.$g $t >/tmp/o 2>&1; echo $g $t exit=\$?"; done
       done
       run "python -m lint.check_secret_headers >/tmp/o 2>&1; echo check_secret_headers exit=\$?" ;;
  g4)  run "pytest -q -p no:cacheprovider tests/gates tests/lint 2>&1 | tail -5" ;;
  *)   run "$*" ;;
esac
