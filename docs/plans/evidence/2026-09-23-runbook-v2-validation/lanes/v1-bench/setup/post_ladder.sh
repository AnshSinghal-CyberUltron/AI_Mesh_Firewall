#!/usr/bin/env bash
# post_ladder.sh — idle baseline, repeats (lowest step + highest error-free step), saturation, DIRECT floors.
set -uo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/v1-bench
export LANE=v1 EVID=$E
source $SP/harness/deploy/lib.sh
PY=/home/contact_cyberultron_com/AI_Mesh_Firewall/gateway/.venv/bin/python
echo "$(date -u +%T) START idle-baseline"
rssh rv-v1-sut-1 "bash ~/sut_step.sh start idle-baseline"; sleep 130; rssh rv-v1-sut-1 "bash ~/sut_step.sh stop idle-baseline" >/dev/null
mkdir -p $E/runs/idle-baseline && rscp_from rv-v1-sut-1 "~/runs/idle-baseline/." $E/runs/idle-baseline/
$PY $E/setup/idle_baseline.py $E/runs/idle-baseline/sampler.jsonl > $E/runs/idle-baseline/idle.json; cat $E/runs/idle-baseline/idle.json
echo "$(date -u +%T) DONE idle-baseline"
bash $E/setup/ladder.sh L1b 1 25
bash $E/setup/ladder.sh L1c 1 25
bash $E/setup/ladder.sh SAT 40
for r in 1 10 25; do
  echo "$(date -u +%T) START D-r$r"
  bash $E/setup/run_step.sh direct "D-r$r" "$r" 60 300 > "$E/runs/D-r$r.log" 2>&1
  echo "$(date -u +%T) DONE D-r$r"; grep -E "Harness analysis|drops|T_addon_total|T_addon_first|T_release_lag_max \||T_fw_addon \|" "$E/runs/D-r$r/analyze.txt" | head -8
done
echo "$(date -u +%T) ALL DONE"
