#!/usr/bin/env bash
# direct_step.sh RUN TOTAL_RATE "LG.." "PROV.." "OLG_FLAGS" ["SYNTHPROV_FLAGS"]  == step.sh with TARGETS unset
unset TARGETS
exec "$(dirname "$0")/step.sh" "$@"
