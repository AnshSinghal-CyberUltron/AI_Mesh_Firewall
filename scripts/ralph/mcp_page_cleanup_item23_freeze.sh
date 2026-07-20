#!/usr/bin/env bash
# MCP-page cleanup item 23 — FREEZE gate. Re-verify sections A–G, 3× each, so the
# whole §1.4 page stays green and alignment/responsive can't regress.
#   A clean errors (01-06) → item21 (all failed servers clean + connected work)
#   B stuck state  (07-09) → item09 (no server stuck at Unknown)
#   C align/respon (10-14) → item12 (SNAPSHOT GATE: 4 widths × 2 themes, no overflow/bleed/touch)
#   D redactions   (15-16) → item16 (API==DB oracle) + item16_ui (230 sanitized both themes)
#   E banner       (17-18) → item17 (up→Protected / slow→Degraded / outage→Offline)
#   F simulator    (19-20) → item19 (default connected + dry-run/live decision)
#   G every tab    (21-22) → item22 (6 tabs × 48 combos, per-server actions, 0 console errors)
set -u
cd /home/contact_cyberultron_com/AI_Mesh_Firewall
export BASE_URL="${BASE_URL:-http://127.0.0.1:8180}"
ROUNDS="${ROUNDS:-3}"
LOG=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/d74b4a3b-4d61-48b7-8ae0-10a05eefc79e/scratchpad/item23_freeze.log
: > "$LOG"

declare -A GATE
GATE[A_cleanerr]="python3 scripts/ralph/mcp_page_cleanup_item21_verify.py"
GATE[B_stuckstate]="node scripts/ralph/mcp_page_cleanup_item09_verify.mjs"
GATE[C_snapshot]="node scripts/ralph/mcp_page_cleanup_item12_verify.mjs"
GATE[D1_redact_api]="python3 scripts/ralph/mcp_page_cleanup_item16_verify.py"
GATE[D2_redact_ui]="node scripts/ralph/mcp_page_cleanup_item16_ui.mjs"
GATE[E_banner]="node scripts/ralph/mcp_page_cleanup_item17_ui.mjs"
GATE[F_simulator]="node scripts/ralph/mcp_page_cleanup_item19_ui.mjs"
GATE[G_everytab]="node scripts/ralph/mcp_page_cleanup_item22_ui.mjs"

# fixed section order
ORDER=(A_cleanerr B_stuckstate C_snapshot D1_redact_api D2_redact_ui E_banner F_simulator G_everytab)

TOTAL=0; PASS=0
declare -A RESULT
# The login view is throttled login_user:5/min per email and every gate signs in
# as the same admin. Space gates (and rounds) so logins stay under the rate; the
# login helpers ALSO retry-on-429 as a backstop.
GATE_GAP="${GATE_GAP:-14}"
ROUND_GAP="${ROUND_GAP:-45}"
for r in $(seq 1 "$ROUNDS"); do
  echo "================ ROUND $r/$ROUNDS ================" | tee -a "$LOG"
  for key in "${ORDER[@]}"; do
    cmd="${GATE[$key]}"
    echo "--- [$r] $key: $cmd" | tee -a "$LOG"
    if timeout 500 bash -c "$cmd" >>"$LOG" 2>&1; then
      RESULT[$r:$key]=PASS; PASS=$((PASS+1)); echo "    [$r] $key => PASS" | tee -a "$LOG"
    else
      rc=$?; RESULT[$r:$key]=FAIL; echo "    [$r] $key => FAIL (rc=$rc)" | tee -a "$LOG"
    fi
    TOTAL=$((TOTAL+1))
    sleep "$GATE_GAP"
  done
  [ "$r" -lt "$ROUNDS" ] && sleep "$ROUND_GAP"
done

echo "================ FREEZE MATRIX ================" | tee -a "$LOG"
printf "%-16s" "gate" | tee -a "$LOG"; for r in $(seq 1 "$ROUNDS"); do printf " R%-3s" "$r" | tee -a "$LOG"; done; echo "" | tee -a "$LOG"
for key in "${ORDER[@]}"; do
  printf "%-16s" "$key" | tee -a "$LOG"
  for r in $(seq 1 "$ROUNDS"); do printf " %-4s" "${RESULT[$r:$key]:-?}" | tee -a "$LOG"; done
  echo "" | tee -a "$LOG"
done
echo "TOTAL $PASS/$TOTAL gates passed across $ROUNDS rounds" | tee -a "$LOG"
if [ "$PASS" -eq "$TOTAL" ]; then echo "ITEM-23-FREEZE: PASS — A–G green ${ROUNDS}x" | tee -a "$LOG"; exit 0; fi
echo "ITEM-23-FREEZE: FAIL" | tee -a "$LOG"; exit 1
