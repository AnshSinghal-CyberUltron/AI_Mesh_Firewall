"""Repro: one tenant's regex / plan body stops the projector round (run from gateway_v2/)."""
import json
import re
import sys

sys.path.insert(0, ".")
from gateway_v2.detect import conditions
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.domain.plan import StreamingMode
from state_control.projector import validate_plan
from tests.state_control.test_gw36_s2_projector import Lab, _rehydrator, KNOBS
from tests.plan.test_lgw05c_delta import _draft


def body_with(pattern: str, slug: str = "acme") -> str:
    body = encode_plan_body(PlanDocument(slug, StreamingMode.INCREMENTAL, (_draft(),)))
    rules = body["rules"]
    rules.append({**rules[0], "rule_id": "deep", "condition": {
        "kind": "regex", "field": "", "direction": rules[0]["scope"], "keywords": [],
        "pattern": pattern, "detector_class": "", "pipeline_stage": ""}})
    return json.dumps(body)


def show(label, fn):
    re.purge()
    try:
        print(f"{label}: returned {fn()!r}"[:240])
    except BaseException as exc:  # noqa: BLE001
        print(f"{label}: RAISED {type(exc).__name__}: {str(exc)[:80]}")


deep = "(" * 499 + "a" + ")" * 499
print("len(deep) =", len(deep), "redos_shape =", conditions._redos_shape(deep))
show("validate_plan(499-deep regex)", lambda: validate_plan("acme", body_with(deep))[1:])
show("validate_plan('a{4294967296}')", lambda: validate_plan("acme", body_with("a{4294967296}"))[1:])
show("validate_plan(deep JSON body)", lambda: validate_plan("acme", "[" * 100000 + "]" * 100000)[1:])


def nested(frames, pattern):
    if frames:
        return nested(frames - 1, pattern)
    return validate_plan("acme", body_with(pattern))[1:]


show("validate_plan(400-deep) at shallow stack", lambda: nested(0, "(" * 400 + "a" + ")" * 400))
show("validate_plan(400-deep) after 600 frames", lambda: nested(600, "(" * 400 + "a" + ")" * 400))


class Boom:
    def finditer(self, text):
        yield re.match("a", "a")
        raise RuntimeError("engine fault mid-scan")


show("_regex_spans(finditer raises RuntimeError after 1 match)",
     lambda: conditions._regex_spans(Boom(), "aaa", 1.0))

lab = Lab()
lab.org("healthy")
lab.org("poisoned")
lab.projector.round_once()
lab.clock.now += KNOBS.lag_ms / 1000 + 1
lab.source.plan("poisoned", body_with(deep, "poisoned"))
lab.source.org("new-healthy")
lab.source.plan("new-healthy", json.dumps(encode_plan_body(
    PlanDocument("new-healthy", StreamingMode.INCREMENTAL, (_draft(),)))))
show("Projector.round_once() with one poisoned org", lambda: lab.projector.round_once())
print("new-healthy plan signed:", "new-healthy" in lab.live(StateKind.PLAN))
stamp = _rehydrator(lab, KNOBS.lag_ms).round_once()
print("rehydrator stamped:", stamp.stamped, "source_lagging:", stamp.source_lagging)

from gateway_v2.domain import Action, Category, FailurePosture, Mode, Rule, RuleCondition, RuleKind, RuleScope, Surface
from gateway_v2.plan.rewrite_template import check_template
rewrite = Rule(rule_id="rw", category=Category.EXFIL, mode=Mode.ENFORCE, action=Action.REWRITE,
               threshold=None, priority=1, scope=RuleScope.OUTPUT,
               on_unavailable=FailurePosture.FAIL_CLOSED, surfaces=frozenset({Surface.CHAT}),
               condition=RuleCondition(kind=RuleKind.REGEX, field="", direction=RuleScope.OUTPUT,
                                       keywords=(), pattern=deep, detector_class="", pipeline_stage=""),
               rewrite_template="Removed by policy {rule_id}")
show("check_template(rewrite rule, 499-deep regex)", lambda: check_template(rewrite))
