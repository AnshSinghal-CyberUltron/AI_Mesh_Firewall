"""Evidence for #117 review B1 / A-S2: what the projector round signs vs what the gateway applies.

Run from gateway_v2/ with the gateway_v2 venv:
    python ../docs/plans/evidence/2026-11-12-projector-review-answers/b1_depth_window.py
Same shape as Reviewer B's `envelope_skew.py`: a valid plan plus an unknown key `extra` holding
nested arrays, which `decode_plan_body` ignores. "depth" below is the TOTAL nesting (body = 1).
The gateway applier is the test `Gateway` (StateTask -> PlanDeltaApplier -> plan_from_record),
run under 0, 200 and 400 nested C-to-Python transitions (`map` callbacks), which consume the
same C recursion budget `json` does.
"""
import logging

from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.domain.state import StateKind
from state_control.projector import validate_plan
from tests.state_control.test_gw36_s2_projector import Lab, _doc
from tests.state_control.test_lgw05c_writer import Gateway

logging.disable(logging.WARNING)


def body(slug: str, depth: int) -> str:
    inner = depth - 1
    return _doc(slug)[:-1] + ', "extra": ' + "[" * inner + '"x"' + "]" * inner + "}"


def via_c(levels, call):
    return call() if levels == 0 else next(map(lambda _: via_c(levels - 1, call), [None]))


import state_control.projector as projector  # noqa: E402

print(f"MAX_PLAN_BODY_DEPTH = {getattr(projector, 'MAX_PLAN_BODY_DEPTH', 'absent (before the fix)')}")
for depth in (5, 31, 32, 33, 9_993, 9_996, 10_000, 100_000):
    verdicts = {lv: (lambda r: "signed" if r[0] is not None else r[2])(
        via_c(lv, lambda: validate_plan("edge", body("edge", depth)))) for lv in (0, 400)}
    lab = Lab()
    for i in range(20):
        lab.org(f"good-{i:02d}")
    lab.org("edge", plan=False)
    lab.source.plan("edge", body("edge", depth))
    summary = lab.projector.round_once()
    signed = "edge" in lab.live(StateKind.PLAN)
    applied = {}
    for lv in (0, 200, 400):
        gateway = Gateway(lab.store)
        try:
            report = via_c(lv, lambda: gateway.round_once(StateKind.PLAN))
            good = all(isinstance(gateway.plans.read(f"good-{i:02d}"), ExecutionPlan) for i in range(20))
            edge = isinstance(gateway.plans.read("edge"), ExecutionPlan) if signed else None
            applied[lv] = f"ok={report.ok} goods={good} edge={edge}"
        except BaseException as exc:  # noqa: BLE001 - the B1 failure mode is an escape
            applied[lv] = f"RAISED {type(exc).__name__}"
    print(f"depth {depth:>6}: validate_plan@0={verdicts[0]!r} @400C={verdicts[400]!r} | round "
          f"heartbeat={summary.heartbeat} invalid_plans={summary.invalid_plans} edge signed={signed}"
          f" | gateway PLAN round @0C: {applied[0]} @200C: {applied[200]} @400C: {applied[400]}")
