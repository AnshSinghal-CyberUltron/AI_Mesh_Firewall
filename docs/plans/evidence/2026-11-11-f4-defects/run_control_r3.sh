#!/bin/bash
# F4 author round 3 (PR #115): targeted control run on a detached mutation worktree at the PR head,
# then one mutant at a time against core.tests.test_v2_plan_rollout, then the rollout bench.
# Run under: flock /home/claude/work/.heavy.lock flock /home/claude/work/.ctl-db.lock nice -n 10 bash run_control_r3.sh
# The settings module is the local pgvector shim (copied from runs/113-A, own test DB name; not committed).
set -u
M=/home/claude/wt/f4-author3-mut
H=/home/claude/work/runs/115-author3/harness
OUT=/home/claude/work/runs/115-author3/mutations
EV=/home/claude/wt/f4-author/docs/plans/evidence/2026-11-11-f4-defects
PY=/home/claude/work/venv-ctl312/bin/python
mkdir -p "$OUT"
cd "$M/control" || exit 2
. /home/claude/work/env-ctl.sh >/dev/null 2>&1
export PYTHONPATH=$H:$EV:$M/control/ai_mesh_control:$M/shared DJANGO_SETTINGS_MODULE=rv113_settings
ROLL=core.tests.test_v2_plan_rollout
NEIGH="core.tests.test_firewall_v2_gw27 core.tests.test_gateway_org_for_unauth core.tests.test_gw33_rewrite_gate tests.test_analytics_db_timeout"
git -C "$M" checkout -q -- . && test -z "$(git -C "$M" status --porcelain)" || { echo "worktree dirty"; exit 2; }
echo "start $(date -u +%FT%TZ) head $(git -C "$M" rev-parse --short=8 HEAD) load $(cut -d' ' -f1-3 /proc/loadavg)" > "$OUT/summary.txt"
s=$(date +%s)
$PY manage.py test $ROLL $NEIGH --noinput -v 2 > "$OUT/00_clean_fresh_db.log" 2>&1
crc=$?
echo "clean (fresh test DB) rc=$crc wall=$(( $(date +%s) - s ))s $(grep -E '^Ran |^OK|^FAILED' "$OUT/00_clean_fresh_db.log" | tr '\n' ' ')" | tee -a "$OUT/summary.txt"
[ "$crc" = 0 ] || { echo "clean run failed; no mutants" | tee -a "$OUT/summary.txt"; exit 1; }
s=$(date +%s)
$PY -m pytest -q -p no:cacheprovider ai_mesh_control/tests/test_control_entrypoint_cgroup_clamp.py > "$OUT/00_entrypoint_pytest.log" 2>&1
echo "entrypoint-clamp pytest rc=$? wall=$(( $(date +%s) - s ))s $(tail -1 "$OUT/00_entrypoint_pytest.log")" | tee -a "$OUT/summary.txt"
R=ai_mesh_control/core/v2_plan_rollout.py
P=ai_mesh_control/core/firewall_v2_publish.py
E=server-entrypoint.sh
mut() { # name file old new
  git -C "$M" checkout -q -- .
  $PY -I - "$M/control/$2" "$3" "$4" <<'PYEOF' || { echo "$1: APPLY FAILED" | tee -a "$OUT/summary.txt"; return; }
import sys
p, old, new = sys.argv[1:4]
s = open(p).read()
assert s.count(old) == 1, (p, old, s.count(old))
open(p, "w").write(s.replace(old, new))
PYEOF
  s=$(date +%s)
  timeout 300 $PY manage.py test $ROLL --noinput --keepdb > "$OUT/$1.log" 2>&1
  rc=$?  # 124: the suite hung (counted as killed; a passing suite never times out)
  fails=$(grep -E '^(FAIL|ERROR): ' "$OUT/$1.log" | sed -E 's/^(FAIL|ERROR): (\S+) .*/\1 \2/' | sort -u | tr '\n' ';')
  echo "$1 rc=$rc wall=$(( $(date +%s) - s ))s $(grep -E '^Ran |^OK|^FAILED' "$OUT/$1.log" | tr '\n' ' ') :: $fails" | tee -a "$OUT/summary.txt"
}
mut M01_code_not_hashed $R '"code": code_digest(),' '"code": "",'
mut M02_migrations_not_hashed $R '"migrations": migrations_digest(),' '"migrations": "",'
mut M03_never_saved_doc_dropped $R '        "never_saved": dict(FirewallConfigSerializer(FirewallConfig()).data),
' ''
mut M04_exfil_hosts_unhashed $R '    "V2_EXFIL_ALLOW_HOSTS",
' ''
mut M05_stale_means_missing_only $R '.exclude(
        v2_plan_document__publisher=current,
        v2_plan_document__publisher_revision=F("v2_plan_document__revision"),
    )' '.exclude(v2_plan_document__isnull=False)'
mut M06_inactive_swept $R 'Organization.objects.filter(is_active=True).exclude(
        v2_plan_document__publisher=current' 'Organization.objects.filter().exclude(
        v2_plan_document__publisher=current'
mut M07_stamp_kept_when_body_same $P '    elif (document.publisher, document.publisher_revision) != (publisher, document.revision):' '    elif False:'
mut M08_stamp_kept_when_body_changes $P '        document.publisher = publisher
' ''
mut M09_tries_not_counted $R '        tries.update(failed if isinstance(failed, list) else ())' '        pass'
mut M10_skip_ignored $R '    if skip:
        orgs = orgs.exclude(pk__in=list(skip))' '    if False:
        orgs = orgs.exclude(pk__in=list(skip))'
mut M11_lost_lock_ignored $R '                    return result("lost_lock")' '                    pass'
mut M12_release_not_owner_checked $R '            lock.release()' '            redis.delete(LOCK_KEY)'
mut M13_lock_not_exclusive $R '    if not lock.acquire(blocking=False):' '    if not (lock.acquire(blocking=False) or True):'
mut M14_migrations_not_awaited $R '    if pending:
        return {' '    if False:
        return {'
mut M15_no_early_stop $R '            if streak >= MAX_CONSECUTIVE_FAILURES:' '            if False:'
mut M16_keyset_not_advanced $R '            last = org.pk
' ''
mut M17_sweep_builds_the_note $R 'publish_for_org(org, publisher=current, notes=False)' 'publish_for_org(org, publisher=current, notes=True)'
mut M18_dev_copy_after_commit $P '            revision = _store(locked, raw, publisher)
            dev_copy = _dev_copy(locked.slug, payload, rows, eligible, rules_mode)
' '            revision = _store(locked, raw, publisher)
        dev_copy = _dev_copy(locked.slug, payload, rows, eligible, rules_mode)
'
mut M20_stamp_revision_not_checked $R '        v2_plan_document__publisher_revision=F("v2_plan_document__revision"),
' ''
mut M21_stamp_revision_not_moved $P '        document.publisher_revision = document.revision
' ''
mut M19_partial_not_reported $R '            return {**note, "status": "partial", "failed": len(left), "failed_orgs": left}' '            return note'
mut E1_no_set_plus_e $E '        set +e
' ''
mut E2_absolute_manage_py $E '        cd "$(dirname "$0")" \
            && "$PYTHON_BIN" manage.py sync_v2_plan_documents --if-changed \' '        cd "$(dirname "$0")" \
            && "$PYTHON_BIN" "$(pwd)/manage.py" sync_v2_plan_documents --if-changed \'
mut E3_no_subshell_head_shape $E '    (
        set +e
        cd "$(dirname "$0")" \
            && "$PYTHON_BIN" manage.py sync_v2_plan_documents --if-changed \
                --attempts "${V2_PLAN_ROLLOUT_ATTEMPTS:-20}" --wait "${V2_PLAN_ROLLOUT_WAIT_S:-30}" \
                2>&1 | sed '"'"'s/^/[control-entrypoint] v2-plan-rollout: /'"'"' >&2
        exit 0
    ) &' '    "$PYTHON_BIN" "$(dirname "$0")/manage.py" sync_v2_plan_documents --if-changed \
        --attempts "${V2_PLAN_ROLLOUT_ATTEMPTS:-20}" --wait "${V2_PLAN_ROLLOUT_WAIT_S:-30}" \
        2>&1 | sed '"'"'s/^/[control-entrypoint] v2-plan-rollout: /'"'"' >&2 &'
mut E4_foreground $E '        exit 0
    ) &' '        exit 0
    )'
git -C "$M" checkout -q -- .
s=$(date +%s)
timeout 1200 $PY manage.py test bench_rollout_r3 --noinput --keepdb > "$OUT/bench.log" 2>&1
echo "bench rc=$? wall=$(( $(date +%s) - s ))s" | tee -a "$OUT/summary.txt"
grep -E "BENCH|^Ran|^OK|^FAILED|Error" "$OUT/bench.log" > "$OUT/bench.out.txt"
echo "end $(date -u +%FT%TZ) load $(cut -d' ' -f1-3 /proc/loadavg) clean=$(test -z "$(git -C "$M" status --porcelain)" && echo yes || echo NO)" | tee -a "$OUT/summary.txt"
