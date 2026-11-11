"""F6 repro: one candidate, upstream 500 -> routing decision recorded as a refusal."""
import asyncio, sys
sys.path.insert(0, ".")
from tests.dispatch._gw28_fakes import FakeClient, Timeline, build_rp, ctx, drain, err
from gateway_v2.edge.routing_trace import RoutingTrace, deciding_rule
from gateway_v2.edge.console_stages import ChatFacts, build_stages

async def main():
    tl = Timeline()
    trace = RoutingTrace(cap=8, alias="m")
    c = ctx()
    c = c.__class__(**{**{f: getattr(c, f) for f in c.__dataclass_fields__}, "sink": trace.append})
    ev = await drain(build_rp({"a": FakeClient("a", [err("upstream_error")], tl)}), c)
    print("opens:", len(tl.opens()), "terminal:", [e.gateway_error for e in ev])
    for d in trace.decisions():
        print("decision:", d.routing_reason, d.selected_candidate, d.refusal_code, d.cause)
    last = trace.decisions()[-1]
    rule = deciding_rule(last, trace.alias)
    facts = ChatFacts(identity_resolved=True, admitted=True, spent=True, input_action="allow",
                      dispatched=True, failure_code="upstream_error", routing_rule=rule,
                      routing_refused=last.refusal_code is not None)
    for s in build_stages(facts):
        print("stage:", s)
asyncio.run(main())
