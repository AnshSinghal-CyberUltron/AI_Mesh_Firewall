"""Author, PR #136 round 2: reviewer B's p2 flow, then one freshness window for the re-check.

Run from <worktree>/gateway_v2 with PYTHONPATH=<worktree>/gateway_v2.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/home/claude/work/runs/136-B/harness")
from fail_open import VICTIM, base, held, settle, victim_state  # noqa: E402
from fleet_attack import KNOBS, body, tamper_sig  # noqa: E402

from gateway_v2.domain.state import StateKind  # noqa: E402


async def main() -> None:
    lab, _keys, _raws = await base()
    lab.writer.plan_set(VICTIM, body(VICTIM, extra_rule=True))
    genuine_v2 = lab.get_plan_raw(VICTIM)
    lab.set_plan_raw(VICTIM, tamper_sig(genuine_v2))
    await settle(lab)
    quarantined = victim_state(lab)
    lab.rehydrator.repair(StateKind.PLAN, "contents")
    await settle(lab)
    same_window = victim_state(lab)
    await asyncio.sleep(KNOBS.fresh_ms / 1000 + 0.1)
    await settle(lab)
    print(f"PROBE p2b_same_position_repair | quarantined={quarantined} | right after the repair "
          f"(same window)={same_window} | one freshness window later={victim_state(lab)} "
          f"held={held(lab, lab.runtime, VICTIM)}")


asyncio.run(main())
