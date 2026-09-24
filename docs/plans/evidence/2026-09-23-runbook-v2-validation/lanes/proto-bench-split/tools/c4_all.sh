#!/usr/bin/env bash
# c4_all.sh RUN...   C4 (tools/c4_client.py, --sample-mod=1 = every stream) for each run -> <run>/c4.json (+ EVID copy)
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/proto-bench-split; RUNS=${RUNS:-/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs}
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
for run in "$@"; do
  d=""; [[ $(python3 -c "import json; print(json.load(open('$RUNS/$run/step.json'))['mode'])") == direct ]] && d="--direct"
  $PY "$E/tools/c4_client.py" "$RUNS/$run" --sample-mod=1 $d > "$RUNS/$run/c4.json" 2> "$RUNS/$run/c4.stderr" && mkdir -p "$E/runs/$run" && cp "$RUNS/$run/c4.json" "$E/runs/$run/"
done
