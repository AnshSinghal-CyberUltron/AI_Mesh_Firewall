"""GW33 task 8.4a/8.4b/M-6: every `compile_plan` call site installs GW26-shaped and REWRITE plans."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from gateway_v2.domain import (
    Action,
    Category,
    ExecutionPlan,
    FailurePosture,
    Mode,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.domain.state import StateKind, Version
from gateway_v2.edge.dev_components import load_dev_plan
from gateway_v2.edge.dev_plan_feed import DevPlanFeed
from gateway_v2.plan.compiler import RuleDraft, compile_plan
from gateway_v2.plan.delta import plan_from_record
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_sig import make_record
from state_control.projector import validate_plan

_ROOT = Path(__file__).resolve().parents[2]
_FROZEN_SITES = frozenset({
    "gateway_v2/edge/dev_components.py",
    "gateway_v2/edge/dev_plan_feed.py",
    "gateway_v2/plan/delta.py",
    "state_control/projector.py",
})
_ORG = "org-a"


def _draft(rule_id: str, category: Category, action: Action, *, scope: RuleScope = RuleScope.OUTPUT,
           condition: RuleCondition | None = None, template: str = "",
           posture: FailurePosture = FailurePosture.FAIL_CLOSED) -> RuleDraft:
    return RuleDraft(rule_id, category, Mode.ENFORCE, action, None, 100, scope, posture,
                     frozenset({Surface.CHAT}), condition, template)


def _cond(kind: RuleKind, **kw: object) -> RuleCondition:
    base: dict[str, object] = {"field": "", "direction": RuleScope.OUTPUT, "keywords": (),
                               "pattern": "", "detector_class": "", "pipeline_stage": ""}
    base.update(kw)
    return RuleCondition(kind=kind, **base)  # type: ignore[arg-type]


def _legacy_plan_drafts(action: Action, *, with_exfil: bool) -> tuple[RuleDraft, ...]:
    drafts = [
        _draft("fw-exfil-output", Category.EXFIL, action),  # the shipped legacy IP-leak rule
        _draft("pol-1-kw", Category.EXFIL, action,
               condition=_cond(RuleKind.KEYWORDS, keywords=("zz-top",))),
        _draft("pol-1-cond", Category.EXFIL, action,
               condition=_cond(RuleKind.REGEX, pattern="ab+c")),
        _draft("pol-1-det", Category.EXFIL, action,
               condition=_cond(RuleKind.DETECTOR, detector_class="customer.thing")),
    ]
    if with_exfil:
        drafts.append(_draft("out_exfil", Category.EXFIL, Action.FLAG,
                             condition=_cond(RuleKind.DETECTOR, detector_class="output.exfil"),
                             posture=FailurePosture.FAIL_OPEN))
    return tuple(drafts)


def _rewrite_drafts() -> tuple[RuleDraft, ...]:
    return (
        _draft("rw-pii", Category.PII, Action.REWRITE, template="[{category} removed]"),
        _draft("rw-ip", Category.IP_LEAK, Action.REWRITE, template="[internal address]",
               condition=_cond(RuleKind.DETECTOR, detector_class="output.ip_leak")),
    )


def _through_all_four(drafts: tuple[RuleDraft, ...], mode: StreamingMode, tmp: Path) -> None:
    body = encode_plan_body(PlanDocument(_ORG, mode, drafts))
    raw = json.dumps(body, sort_keys=True).encode()
    # 1. plan/delta.py: the production record-to-plan path.
    record = make_record(b"gw33-secret", StateKind.PLAN, _ORG, body, Version(1, 1), 1)
    assert plan_from_record(record, compiled_at=1.0).streaming_mode is mode
    # 2. edge/dev_plan_feed.py
    boot = compile_plan(_ORG, (_draft("boot", Category.PII, Action.REDACT),), compiled_at=0.0)
    feed = DevPlanFeed(boot, now=0.0)
    assert feed.apply(raw, compiled_at=2.0, now=1.0) not in ("rejected", "unchanged")
    served = feed.source().lookup(_ORG, now=1.0)
    assert isinstance(served, ExecutionPlan) and served.streaming_mode is mode
    # 3. edge/dev_components.py
    path = tmp / f"plan-{mode.value}.json"
    path.write_bytes(raw)
    assert load_dev_plan(str(path), org_id=_ORG, compiled_at=1.0).streaming_mode is mode
    # 4. state_control/projector.py (control-side validation)
    signed, _refused, why = validate_plan(_ORG, raw.decode())
    assert signed is not None and why == ""


@pytest.mark.parametrize("action", [Action.REDACT, Action.BLOCK])
@pytest.mark.parametrize("mode", list(StreamingMode))
@pytest.mark.parametrize("with_exfil", [False, True])
def test_gw26_shaped_plans_install_through_all_four_sites(
    action: Action, mode: StreamingMode, with_exfil: bool, tmp_path: Path,
) -> None:
    _through_all_four(_legacy_plan_drafts(action, with_exfil=with_exfil), mode, tmp_path)


@pytest.mark.parametrize("mode", list(StreamingMode))
def test_a_rewrite_plan_compiles_through_all_four_sites(mode: StreamingMode, tmp_path: Path) -> None:
    drafts = _rewrite_drafts()
    if mode is StreamingMode.INCREMENTAL:  # ip_leak + rewrite is strict-only (D-9)
        drafts = drafts[:1]
    _through_all_four(drafts, mode, tmp_path)


def test_every_production_compile_plan_call_is_in_the_frozen_list() -> None:
    """A fifth call site must be added to this list (and its install tested) consciously."""
    found: set[str] = set()
    for base in (_ROOT / "gateway_v2", _ROOT / "state_control", _ROOT / "audit_control"):
        for path in base.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "compile_plan":
                    found.add(path.relative_to(_ROOT).as_posix())
    assert found == _FROZEN_SITES
