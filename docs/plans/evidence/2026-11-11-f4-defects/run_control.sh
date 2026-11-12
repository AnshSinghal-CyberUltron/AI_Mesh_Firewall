#!/bin/bash
# F4 author (PR #115): clean targeted control run on the mutation worktree, then one mutation at a time.
# Run under: flock /home/claude/work/.heavy.lock flock /home/claude/work/.ctl-db.lock nice -n 10 bash run_control.sh
set -u
M=/home/claude/wt/f4-author-mut
H=/home/claude/work/runs/115-author/harness
OUT=/home/claude/work/runs/115-author/mutations
PY=/home/claude/work/venv-ctl312/bin/python
cd "$M/control" || exit 2
. /home/claude/work/env-ctl.sh >/dev/null 2>&1
export PYTHONPATH=$H:$M/control/ai_mesh_control:$M/shared DJANGO_SETTINGS_MODULE=rv113_settings
ROLL=core.tests.test_v2_plan_rollout
NEIGH="core.tests.test_firewall_v2_gw27 core.tests.test_gateway_org_for_unauth core.tests.test_gw33_rewrite_gate tests.test_analytics_db_timeout"
git -C "$M" checkout -q -- . && test -z "$(git -C "$M" status --porcelain)" || { echo "worktree dirty"; exit 2; }
echo "start $(date -u +%FT%TZ) load $(cut -d' ' -f1-3 /proc/loadavg)" > "$OUT/summary.txt"
s=$(date +%s)
$PY manage.py test $ROLL $NEIGH --noinput --keepdb -v 2 > "$OUT/00_clean.log" 2>&1
echo "clean rc=$? wall=$(( $(date +%s) - s ))s $(grep -E '^Ran |^OK|^FAILED' "$OUT/00_clean.log" | tr '\n' ' ')" | tee -a "$OUT/summary.txt"
s=$(date +%s)
$PY -m pytest -q -p no:cacheprovider ai_mesh_control/tests/test_control_entrypoint_cgroup_clamp.py > "$OUT/00_entrypoint_pytest.log" 2>&1
echo "entrypoint-clamp pytest rc=$? wall=$(( $(date +%s) - s ))s $(tail -1 "$OUT/00_entrypoint_pytest.log")" | tee -a "$OUT/summary.txt"
R=ai_mesh_control/core/v2_plan_rollout.py
mut() { # name file python-old python-new
  git -C "$M" checkout -q -- .
  $PY -I - "$M/control/$2" "$3" "$4" <<'PYEOF' || { echo "$1: APPLY FAILED" | tee -a "$OUT/summary.txt"; return; }
import sys
p, old, new = sys.argv[1:4]
s = open(p).read()
assert s.count(old) == 1, (p, old, s.count(old))
open(p, "w").write(s.replace(old, new))
PYEOF
  s=$(date +%s)
  $PY manage.py test $ROLL --noinput --keepdb > "$OUT/$1.log" 2>&1
  rc=$?
  fails=$(grep -E '^(FAIL|ERROR): ' "$OUT/$1.log" | sed -E 's/^(FAIL|ERROR): (\S+) .*/\1 \2/' | sort | tr '\n' ';')
  echo "$1 rc=$rc wall=$(( $(date +%s) - s ))s $(grep -E '^Ran |^OK|^FAILED' "$OUT/$1.log" | tr '\n' ' ') :: $fails" | tee -a "$OUT/summary.txt"
}
mut MR1_marker_ignored $R 'if not force and _text(redis.get(MARKER_KEY)) == current:' 'if False:'
mut MR2_marker_on_failure $R '        if failed == 0:
            redis.set(MARKER_KEY, current)' '        if True:
            redis.set(MARKER_KEY, current)'
mut MR3_settings_not_hashed $R '{"document": document.decode("utf-8"), "settings": _settings()}' '{"document": document.decode("utf-8"), "settings": {}}'
mut MR4_inactive_swept $R 'Organization.objects.filter(is_active=True, pk__gt=last)' 'Organization.objects.filter(pk__gt=last)'
mut MR5_lock_not_exclusive $R 'redis.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL_S)' 'redis.set(LOCK_KEY, token, ex=LOCK_TTL_S)'
mut MR6_keyset_off_by_one $R 'last = batch[-1].pk' 'last = batch[0].pk'
mut MR7_entrypoint_foreground server-entrypoint.sh "2>&1 | sed 's/^/[control-entrypoint] v2-plan-rollout: /' >&2 &" "2>&1 | sed 's/^/[control-entrypoint] v2-plan-rollout: /' >&2"
git -C "$M" checkout -q -- .
s=$(date +%s)
$PY manage.py test $ROLL $NEIGH --noinput > "$OUT/99_clean_fresh_db.log" 2>&1
echo "clean-fresh-db (drops the test DB) rc=$? wall=$(( $(date +%s) - s ))s $(grep -E '^Ran |^OK|^FAILED' "$OUT/99_clean_fresh_db.log" | tr '\n' ' ')" | tee -a "$OUT/summary.txt"
echo "end $(date -u +%FT%TZ) clean=$(test -z "$(git -C "$M" status --porcelain)" && echo yes || echo NO)" | tee -a "$OUT/summary.txt"
