"""GW27 Task 3: every ``compile_plan`` site forwards ``document.controls``."""
from __future__ import annotations

import ast
from pathlib import Path

from gateway_v2.domain.plan import (
    AuditControl,
    ExecutionPlan,
    OrgControls,
    RateControl,
    StreamingMode,
)
from gateway_v2.domain.state import StateKind, StateOp, Version
from gateway_v2.edge.dev_components import load_dev_plan
from gateway_v2.edge.dev_plan_feed import DevPlanFeed
from gateway_v2.plan.compiler import compile_plan
from gateway_v2.plan.delta import plan_from_record
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.runtime.state_sig import make_record
from tests.plan.test_lgw05c_delta import SECRET, _draft

CONTROLS = OrgControls(
    rate=RateControl(True, 600, 5),
    org_tpm_limit=1234,
    max_response_tokens=99,
    audit=AuditControl(True, True, 30),
    compliance_frameworks=("SOC2",),
)
_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def _document() -> PlanDocument:
    return PlanDocument("org-a", StreamingMode.INCREMENTAL, (_draft(),), CONTROLS)


def test_production_feed_path_carries_controls() -> None:
    record = make_record(
        SECRET, StateKind.PLAN, "org-a", encode_plan_body(_document()), Version(1, 1), 1,
        deleted=False, op=StateOp.PUT,
    )
    assert plan_from_record(record, compiled_at=1.0).controls == CONTROLS


def test_dev_file_path_carries_controls(tmp_path: Path) -> None:
    import json

    path = tmp_path / "plan.json"
    path.write_text(json.dumps(encode_plan_body(_document())), encoding="utf-8")
    plan = load_dev_plan(str(path), org_id="org-a", compiled_at=1.0)
    assert plan.controls == CONTROLS


def test_dev_published_feed_path_carries_controls() -> None:
    import json

    seed = compile_plan("org-a", (_draft("seed"),), compiled_at=1.0)
    feed = DevPlanFeed(seed, now=1.0)
    raw = json.dumps(encode_plan_body(_document())).encode()
    assert feed.apply(raw, compiled_at=2.0, now=2.0) not in {"rejected", "unchanged"}
    served = feed.source().lookup("org-a", 2.0)
    assert isinstance(served, ExecutionPlan) and served.controls == CONTROLS


def test_every_compile_plan_call_passes_controls() -> None:
    missing: list[str] = []
    for path in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "compile_plan" and not any(k.arg == "controls" for k in node.keywords):
                    missing.append(f"{path.name}:{node.lineno}")
    assert missing == [], missing
