"""Mutation test: undo each mechanism of the fix in turn; the new tests must fail, then pass again.

Run from the repository root of a clean checkout of this branch:

    python docs/plans/evidence/2026-11-12-state-applier-isolation/mutation_test.py <python>

Each mutation is a literal text replacement in ONE product file (asserted to apply exactly once),
the test modules in TESTS are run, and the file is restored with `git checkout` -- so run it on a
COMMITTED tree. A mutation that leaves every test green is reported as SURVIVED.

Round 2 (the #136 reviews) added reviewer A's MA*, reviewer B's BM* and one R* per new guard.
Two survive and are argued equivalent in the PR: MA8 (= BM3; the snapshot's early return for a
quarantined tenant, killed by BM1, precedes the cache `forget` empties) and R24 (an early exit
for an index entry that moved on; the pairing check right after it refuses the same record).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
GW = ROOT / "gateway_v2"
TESTS = [
    "tests/runtime/test_state_applier_isolation.py",
    "tests/edge/test_state_applier_isolation_e2e.py",
    "tests/state_control/test_generic_handlers_log_type_only.py",
    # The #136 review round (reviewer A 5870729191, reviewer B 5870905487):
    "tests/runtime/test_state_quarantine.py",
    "tests/edge/test_state_quarantine_e2e.py",
    "tests/admit/test_lgw29_revocation_property.py",
]

MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "M1 round_once catches only StoreDataUnavailable from the applier (develop)",
        "gateway_v2/runtime/state_task.py",
        "        except Exception as exc:  # noqa: BLE001 - the applier refused the delta; cursor stays",
        "        except StoreDataUnavailable as exc:",
    ),
    (
        "M2 the plan applier catches only CompileError (develop)",
        "gateway_v2/plan/delta.py",
        "            except Exception as exc:  # noqa: BLE001 - one tenant's body never stops the round",
        "            except CompileError as exc:",
    ),
    (
        "M3 the reader ignores isolate=True (develop: one bad record refuses the round)",
        "gateway_v2/runtime/state_feed.py",
        "            if not isolate:\n",
        "            if True:\n",
    ),
    (
        "M4 _envelope does not catch RecursionError (develop)",
        "gateway_v2/runtime/state_sig.py",
        "    except RecursionError as exc:\n"
        "        raise StoreDataUnavailable(f\"{where} is nested too deeply to be an envelope\") from exc\n",
        "",
    ),
    (
        "M5 the snapshot serves last-known-good to a quarantined tenant",
        "gateway_v2/plan/snapshot.py",
        "        if state.reason == PLAN_QUARANTINED:",
        "        if False:",
    ),
    (
        "M6 no depth bound on plan bodies",
        "gateway_v2/plan/document.py",
        "    if depth > MAX_BODY_DEPTH:",
        "    if False:",
    ),
    (
        "M7 kill-switch drift latch only on StoreDataUnavailable (develop)",
        "gateway_v2/admit/killswitch.py",
        "        except Exception:\n            # Drift latch (C3)",
        "        except StoreDataUnavailable:\n            # Drift latch (C3)",
    ),
    (
        "M8a state loop logs exception text (develop)",
        "gateway_v2/edge/state_runtime.py",
        'LOG.error("state cycle failed: %s", type(exc).__name__)',
        'LOG.error("state cycle failed: %s: %s", type(exc).__name__, exc)',
    ),
    (
        "M8b projector service logs exception text (develop)",
        "state_control/projector_service.py",
        'LOG.warning("projection round failed: %s", type(exc).__name__)',
        'LOG.warning("projection round failed: %s: %s", type(exc).__name__, exc)',
    ),
    (
        "M8c re-hydrator logs exception text (develop)",
        "state_control/rehydrate.py",
        '"rehydrate round failed for kind=%s: %s", kind.value, type(exc).__name__,',
        '"rehydrate round failed for kind=%s: %s", kind.value, exc,',
    ),
    (
        "M9 a plan rejection records the exception text",
        "gateway_v2/plan/delta.py",
        "                error = type(exc).__name__\n",
        "                error = f\"{type(exc).__name__}: {exc}\"\n",
    ),
    (
        "M10 the KEY applier ignores faults",
        "gateway_v2/edge/state_runtime.py",
        "            if round_.faults:\n                # A KEY record that failed verification",
        "            if False:\n                # A KEY record that failed verification",
    ),
    (
        "M11 a faulted plan record keeps last-good instead of failing closed",
        "gateway_v2/plan/delta.py",
        "            self._store.quarantine(fault.key, feed_seq=fault.feed_seq, error=fault.error)",
        "            self._store.register_tenant(fault.key)",
    ),
    (
        "M12 a faulted plan record is ignored (cursor moves, nothing quarantined)",
        "gateway_v2/plan/delta.py",
        "            quarantined=self._quarantine(faults),",
        "            quarantined=(),",
    ),
    # --- the #136 review round: reviewer A's MA*, reviewer B's BM*, and one per new guard (R*) --
    (
        "MA1/BM5 _in_flight drops the manifest bound (a moved index entry is skipped, not faulted)",
        "gateway_v2/runtime/state_feed.py",
        "    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq",
        "    return record.key == key and record.feed_seq > score",
    ),
    (
        "MA2 _in_flight drops the key check (another key's newer record is skipped)",
        "gateway_v2/runtime/state_feed.py",
        "    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq",
        "    return record.feed_seq > score and record.feed_seq > manifest.feed_seq",
    ),
    (
        "MA3 cursor from the last VERIFIED record, not the last index entry",
        "gateway_v2/runtime/state_feed.py",
        "        last = page.entries[-1][1]\n",
        "        last = records[-1].feed_seq if records else cursor.feed_seq\n",
    ),
    (
        "MA4 _already_served orders against read(), not current()",
        "gateway_v2/plan/delta.py",
        "        current = self._store.current(plan.org_id)\n        if current is None:\n",
        "        current = self._store.read(plan.org_id)\n"
        "        if not isinstance(current, ExecutionPlan):\n",
    ),
    (
        "MA5 the isolating reader catches only StoreDataUnavailable",
        "gateway_v2/runtime/state_feed.py",
        "except Exception as exc:  # noqa: BLE001 - one record never refuses the round here",
        "except StoreDataUnavailable as exc:",
    ),
    (
        "MA6 the stamp read catches only StoreDataUnavailable",
        "gateway_v2/runtime/state_task.py",
        "        except Exception as exc:  # noqa: BLE001 - a stamp defect never stops the kinds' drains\n",
        "        except StoreDataUnavailable as exc:\n",
    ),
    (
        "MA7 a KEY fault does not invalidate the GCRA cache",
        "gateway_v2/edge/state_runtime.py",
        "                self._gcra_invalidate(faulted)\n",
        "                pass\n",
    ),
    (
        "MA8/BM3 a quarantine does not forget the snapshot's last-known-good (expected EQUIVALENT)",
        "gateway_v2/plan/delta.py",
        "            if self._snapshot is not None:\n                self._snapshot.forget(fault.key)\n",
        "",
    ),
    (
        "MA9 nesting_depth without the escape passes",
        "gateway_v2/plan/document.py",
        """    unescaped = raw.replace(b"\\\\\\\\", b"").replace(b'\\\\"', b"")\n""",
        "    unescaped = raw\n",
    ),
    (
        "MA10 a verified no-op record does not clear what is held back",
        "gateway_v2/plan/delta.py",
        "                self._store.clear_held(org_id, feed_seq=record.feed_seq)\n",
        "",
    ),
    (
        "BM1 the snapshot's early return for a quarantined tenant is removed",
        "gateway_v2/plan/snapshot.py",
        "        if state.reason == PLAN_QUARANTINED:",
        "        if False and state.reason == PLAN_QUARANTINED:",
    ),
    (
        "BM2 PlanStore.read ignores a quarantine (the held plan is served)",
        "gateway_v2/plan/store.py",
        "            if held is not None and held.state == QUARANTINED:\n                return PlanUnavailable",
        "            if False:\n                return PlanUnavailable",
    ),
    (
        "BM4 the plan applier ignores faults (cursor still moves)",
        "gateway_v2/plan/delta.py",
        "            self._store.quarantine(fault.key, feed_seq=fault.feed_seq, error=fault.error)",
        "            pass",
    ),
    (
        "BM6 _in_flight also swallows an OLDER record under a newer score (a replay)",
        "gateway_v2/runtime/state_feed.py",
        "    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq",
        "    return record.key == key and record.feed_seq != score",
    ),
    (
        "BM7 KS and BUDGET are isolated too",
        "gateway_v2/edge/state_runtime.py",
        "ISOLATED_KINDS = frozenset({StateKind.PLAN, StateKind.KEY})",
        "ISOLATED_KINDS = frozenset({StateKind.PLAN, StateKind.KEY, StateKind.KS, StateKind.BUDGET})",
    ),
    (
        "BM8/B1 a KEY fault only evicts (the reviewed head: no quarantine)",
        "gateway_v2/edge/state_runtime.py",
        "                self.identity_cache.quarantine(\n"
        "                    ((fault.key, fault.feed_seq) for fault in round_.faults),\n",
        "                self.identity_cache.evict(\n"
        "                    (fault.key for fault in round_.faults),\n",
    ),
    (
        "R1 fetch_key does not pair the record with its index entry (B1, B's P2)",
        "gateway_v2/edge/state_runtime.py",
        "            if score is None or not record_matches_index(record, score):\n",
        "            if False:\n",
    ),
    (
        "R2 fetch_key answers an indexed key with no record as absent (A's N3)",
        "gateway_v2/edge/state_runtime.py",
        '                    raise StoreDataUnavailable("key is indexed but its record is missing")\n',
        "                    return None\n",
    ),
    (
        "R3 a quarantined key's fill is not refused below its position",
        "gateway_v2/admit/identity.py",
        "            if floor is not None and (principal is None or principal.feed_seq < floor):\n",
        "            if False:\n",
    ),
    (
        "R4 a verified KEY delta record does not lift a quarantine",
        "gateway_v2/admit/identity.py",
        "            self.lift_quarantine(record.key, record.feed_seq)\n",
        "",
    ),
    (
        "R5 a verified, paired fetch does not lift a quarantine",
        "gateway_v2/edge/state_runtime.py",
        "            self.identity_cache.lift_quarantine(key_hash, record.feed_seq)\n",
        "",
    ),
    (
        "R6 a quarantine lifts for a record below its position",
        "gateway_v2/admit/identity.py",
        "        if at is None or feed_seq < at:\n",
        "        if at is None:\n",
    ),
    (
        "R7 a KEY fault does not tell the revocation ledger",
        "gateway_v2/edge/state_runtime.py",
        "                    self._revocation_refuse(fault.key, fault.feed_seq)\n",
        "                    pass\n",
    ),
    (
        "R8 a rejection lifts a quarantine (the reviewed head)",
        "gateway_v2/plan/store.py",
        "            if held is not None and held.state == QUARANTINED and held.feed_seq != feed_seq:\n",
        "            if False:\n",
    ),
    (
        "R9 a rejection keeps a quarantine even for the quarantined record itself",
        "gateway_v2/plan/store.py",
        "            if held is not None and held.state == QUARANTINED and held.feed_seq != feed_seq:\n",
        "            if held is not None and held.state == QUARANTINED:\n",
    ),
    (
        "R10 clear_held lifts a quarantine from below its position",
        "gateway_v2/plan/store.py",
        "            if held is None or (held.state == QUARANTINED and feed_seq < held.feed_seq):\n",
        "            if held is None:\n",
    ),
    (
        "R11 drain reports the last round's counts only (the reviewed head)",
        "gateway_v2/runtime/state_task.py",
        "                quarantined=total.quarantined + report.quarantined,\n",
        "                quarantined=report.quarantined,\n",
    ),
    (
        "R12 drain sums no rejections",
        "gateway_v2/runtime/state_task.py",
        "                rejected=total.rejected + report.rejected,\n",
        "                rejected=report.rejected,\n",
    ),
    (
        "R13 the in-flight skip is not counted",
        "gateway_v2/runtime/state_feed.py",
        "                    in_flight += 1\n",
        "",
    ),
    (
        "R14 the held-back gauge is not observed",
        "gateway_v2/edge/state_runtime.py",
        "        self.metrics.observe_held_back(StateKind.PLAN, rejected=rejected, quarantined=quarantined)\n",
        "",
    ),
    (
        "R15 a standing StoreDataUnavailable refusal is not logged",
        "gateway_v2/runtime/state_task.py",
        '            if self._failing.get(kind, ("", ""))[0] != shape:\n                LOG.warning(\n',
        "            if False:\n                LOG.warning(\n",
    ),
    (
        "R16 refusals are de-duplicated on the raw message (positions included)",
        "gateway_v2/runtime/state_task.py",
        '            shape = _DIGITS.sub("N", error)\n',
        "            shape = error\n",
    ),
    (
        "R17 the refusal detail is not cut",
        "gateway_v2/runtime/state_task.py",
        "detail=%.160s",
        "detail=%s",
    ),
    (
        "R18 a stamp defect is logged every cycle",
        "gateway_v2/runtime/state_task.py",
        "            if self._stamp_failing != error:\n",
        "            if True:\n",
    ),
    (
        "R19 a non-ASCII signature reaches hmac.compare_digest (TypeError)",
        "gateway_v2/runtime/state_sig.py",
        "    return signature.isascii() and hmac.compare_digest(signature, expected)\n",
        "    return hmac.compare_digest(signature, expected)\n",
    ),
    (
        "R20 unencodable text is not refused by the decoder (UnicodeEncodeError)",
        "gateway_v2/runtime/state_sig.py",
        "    if not value.isascii():\n",
        "    if False:\n",
    ),
    (
        "R21 a non-integral index score is truncated into a pairing position",
        "gateway_v2/runtime/store_valkey.py",
        "    if isinstance(value, float) and not value.is_integer():\n",
        "    if False:\n",
    ),
    (
        "R22 the quarantine re-check never runs",
        "gateway_v2/edge/state_runtime.py",
        "            await self._recheck_isolated()\n",
        "            pass\n",
    ),
    (
        "R23 the PLAN re-check does not pair the record with the quarantined position",
        "gateway_v2/edge/state_runtime.py",
        "        if record.key != org_id or not record_matches_index(record, at):\n",
        "        if record.key != org_id:\n",
    ),
    (
        "R24 the PLAN re-check applies a record whose index entry moved on",
        "gateway_v2/edge/state_runtime.py",
        "            if raw is None or score != at:\n",
        "            if raw is None:\n",
    ),
    (
        "R25 the escaped-quote pass is dropped",
        "gateway_v2/plan/document.py",
        """.replace(b'\\\\"', b"")""",
        "",
    ),
    (
        "R26 the depth scan's running sum restarts at 0 in every slice",
        "gateway_v2/plan/document.py",
        "accumulate(map(_STEP.__getitem__, part), initial=depth)",
        "accumulate(map(_STEP.__getitem__, part), initial=0)",
    ),
    (
        "R27 the depth scan does not carry the depth to the next slice",
        "gateway_v2/plan/document.py",
        '        depth += part.count(b"[") + part.count(b"{") - part.count(b"]") - part.count(b"}")\n',
        "",
    ),
]


def _pytest(python: str) -> tuple[int, str]:
    done = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=GW, env={"PYTHONPATH": str(GW), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )
    lines = [line for line in done.stdout.splitlines() if " passed" in line or " failed" in line]
    return done.returncode, (lines[-1] if lines else done.stdout[-300:])


def main(python: str) -> int:
    code, summary = _pytest(python)
    print(f"baseline (fix intact): exit={code} {summary}")
    if code != 0:
        return 1
    survived = 0
    for label, rel, old, new in MUTATIONS:
        path = GW / rel
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, (label, text.count(old))
        path.write_text(text.replace(old, new), encoding="utf-8")
        try:
            code, summary = _pytest(python)
        finally:
            subprocess.run(["git", "checkout", "--", str(path)], cwd=ROOT, check=True)
        verdict = "KILLED" if code != 0 else "SURVIVED"
        survived += code == 0
        print(f"{verdict:8} {label}: {summary}")
    code, summary = _pytest(python)
    print(f"restored (fix intact): exit={code} {summary}")
    return 1 if survived or code else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
