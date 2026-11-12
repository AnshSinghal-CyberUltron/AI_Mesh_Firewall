"""Cost of the plan-body depth bound (`plan.document.nesting_depth`) and of decoding with it.

Run from gateway_v2/ with PYTHONPATH=. :

    PYTHONPATH=. python ../docs/plans/evidence/2026-11-12-state-applier-isolation/bench_depth_bound.py

Reports thread CPU time and the wall-clock median over repeats, per body:

* realistic bodies of 1, 50 and 500 rules (what the encoder publishes);
* 1 MiB adversarial bodies built to stress each regex pass: all brackets, all two-byte escapes
  inside one string, all empty strings, and an unterminated string;
* `decode_plan_body` end to end, so the bound's share of a decode is visible.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Callable

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.plan.compiler import CompileError
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body, nesting_depth
from tests.plan.test_lgw05c_delta import _draft

MIB = 1 << 20
REPEATS = 15


def _plan(rules: int) -> bytes:
    drafts = tuple(_draft(f"rule-{n}") for n in range(rules))
    return json.dumps(encode_plan_body(PlanDocument("org", StreamingMode.INCREMENTAL, drafts))).encode()


def _measure(work: Callable[[], object]) -> tuple[float, float]:
    walls: list[float] = []
    cpus: list[float] = []
    for _ in range(REPEATS):
        cpu0, wall0 = time.thread_time(), time.perf_counter()
        work()
        cpus.append(time.thread_time() - cpu0)
        walls.append(time.perf_counter() - wall0)
    return statistics.median(cpus) * 1000, statistics.median(walls) * 1000


def _decode(raw: bytes) -> object:
    try:
        return decode_plan_body(raw)
    except CompileError:
        return None


def main() -> int:
    bodies = {
        "plan 1 rule": _plan(1),
        "plan 50 rules": _plan(50),
        "plan 500 rules": _plan(500),
        "1 MiB '[' (refused)": b"[" * MIB,
        "1 MiB escapes in one string": b'"' + b"\\n" * (MIB // 2) + b'"',
        "1 MiB empty strings": b'""' * (MIB // 2),
        "1 MiB unterminated string": b'"' + b"a" * MIB,
    }
    print(f"load average before: {os.getloadavg()}")
    print(f"{'body':32} {'bytes':>9} {'depth':>6} {'bound cpu ms':>13} {'bound wall ms':>14} "
          f"{'decode cpu ms':>14} {'decode wall ms':>15}")
    for name, raw in bodies.items():
        depth = nesting_depth(raw)
        bound_cpu, bound_wall = _measure(lambda raw=raw: nesting_depth(raw))
        decode_cpu, decode_wall = _measure(lambda raw=raw: _decode(raw))
        print(f"{name:32} {len(raw):>9} {depth:>6} {bound_cpu:>13.3f} {bound_wall:>14.3f} "
              f"{decode_cpu:>14.3f} {decode_wall:>15.3f}")
    print(f"load average after: {os.getloadavg()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
