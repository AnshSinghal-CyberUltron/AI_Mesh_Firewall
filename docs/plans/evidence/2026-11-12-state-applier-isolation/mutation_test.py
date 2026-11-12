"""Mutation test: undo each mechanism of the fix in turn; the new tests must fail, then pass again.

Run from the repository root of a clean checkout of this branch:

    python docs/plans/evidence/2026-11-12-state-applier-isolation/mutation_test.py <python>

Each mutation is a literal text replacement in ONE product file (asserted to apply exactly once),
the four new test modules are run, and the file is restored with `git checkout`. A mutation that
leaves every test green is reported as SURVIVED.
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
