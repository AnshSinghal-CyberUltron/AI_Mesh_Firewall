"""Exercise each cross-layer name the spec needs at RUNTIME; report which ones break
when the import is hidden under TYPE_CHECKING (variant v2_tc_all)."""
import asyncio, traceback
from gateway_v2.plan.model import (Action, Category, ExecutionPlan, FailurePosture, Mode,
                                   PlatformIntegrity, Rule, RuleScope, StreamingMode)
import gateway_v2.detect.base as db
import gateway_v2.resolve.decision as rd

plan = ExecutionPlan("org", "v1", 0.0,
    (Rule("r", Category.PII, Mode.ENFORCE, Action.REDACT, None, 1, RuleScope.BOTH, FailurePosture.FAIL_CLOSED),),
    frozenset(), StreamingMode.STRICT_WITHHOLD, PlatformIntegrity(True))

def mk_finding():
    return object.__new__(db.Finding)  # bypass __post_init__ to reach later edges

class Det:
    detector_id = "d"
    def detect(self, text, plan):
        f = object.__new__(db.Finding)
        for k, v in dict(detector="d", detector_version="1", category=Category.PII,
                         status=db.FindingStatus.EXECUTED, confidence=0.9, spans=(), evidence=None).items():
            object.__setattr__(f, k, v)
        return (f,)

async def drain(gen):
    return [c async for c in gen]

class Up:
    def __aiter__(self):
        async def g():
            yield b"a"
        return g()

probes = {
  "detect->plan   (Finding.__post_init__ validates Category, LGW04-3)":
      lambda: db.Finding("d", "1", Category.PII, db.FindingStatus.EXECUTED, 0.9, (), None),
  "resolve->detect+plan (resolver compares FindingStatus / Mode at runtime)":
      lambda: __import__("gateway_v2.resolve.resolver", fromlist=["x"]).resolve(Det().detect("", plan), plan, rd.Phase.INPUT),
  "dispatch->resolve (entry point isinstance(DispatchAuthorization))":
      lambda: __import__("gateway_v2.dispatch.provider", fromlist=["x"]).require_authorization(object()),
  "egress->plan  (StreamPipeline reads StreamingMode)":
      lambda: asyncio.run(drain(__import__("gateway_v2.egress.stream", fromlist=["x"]).StreamPipeline().run(
          Up(), __import__("gateway_v2.egress.stream", fromlist=["x"]).OutputContext(plan)))),
  "egress->resolve (output_guard calls resolve(), Phase.OUTPUT)":
      lambda: __import__("gateway_v2.egress.output_guard", fromlist=["x"]).guard_text("t", [Det()], plan),
}
for name, fn in probes.items():
    try:
        r = fn()
        print(f"OK        {name}")
    except NameError as e:
        print(f"NameError {name}: {e}")
    except TypeError as e:
        print(f"TypeError {name}: {e}  (expected rejection => name resolved at runtime)")
