"""P10 (GW07): priority semantics let a higher-priority dispatching rule cancel a BLOCK on a DIFFERENT finding.
Benchmarked resolve/resolver.py + dispatch/transform.py.  Prompt carries an email (PII) and an AWS key (secret).
LGW07-6 expects: "PII=REDACT and Credentials=BLOCK on one prompt -> BLOCK by priority; FAIL: redaction cancels the block"."""
import copy, sys
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.edge.wire import parse_chat
from rvproto.detect.canon import canonicalize
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import Verifier
from rvproto.dispatch.transform import apply_and_verify
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.resolve.resolver import resolve, authorize
from rvproto.domain.plan import Phase
from rvproto.domain.findings import Finding, FindingStatus, DETECTORS, SEMANTIC_DETECTOR
KEY, MAIL = "AKIAQYLPMN5HHHFPZAM2", "alice.canary@example.com"
chat = parse_chat(orjson.dumps({"model": "m", "messages": [{"role": "user", "content": f"mail {MAIL} key {KEY}"}]}))
det = DeterministicDetectors(Matcher()); sem = Finding(SEMANTIC_DETECTOR, "v", DETECTORS[SEMANTIC_DETECTOR], FindingStatus.EXECUTED, 0.0, (), None)
def variant(pii_prio, secret_prio, pii_id="A.pii.in", secret_id="A.secret.in", pii_action="redact"):
    d = org_a(); d = copy.deepcopy(d)
    for r in d["rules"]:
        if r["rule_id"] == "A.pii.in": r.update(priority=pii_prio, rule_id=pii_id, action=pii_action)
        if r["rule_id"] == "A.secret.in": r.update(priority=secret_prio, rule_id=secret_id)
    return compile_plan(d)
cases = [("fixture: secret 20 < pii 30", variant(30, 20)),
         ("pii REDACT prio 10, secret BLOCK prio 20", variant(10, 20)),
         ("equal prio 20, ids A.pii.in / A.secret.in", variant(20, 20)),
         ("equal prio 20, ids Z.pii.in / A.secret.in (rename only)", variant(20, 20, pii_id="Z.pii.in")),
         ("pii FLAG prio 10, secret BLOCK prio 20", variant(10, 20, pii_action="flag"))]
for name, plan in cases:
    canon = [canonicalize(s.text, 4096) for s in chat.segments]
    dec = resolve((*det.scan(canon, plan.required_input), sem), plan, Phase.INPUT)
    auth = authorize(dec, "rid")
    if auth is None:
        out = "403, zero provider calls"
    else:
        body = apply_and_verify(chat, dec.transformations, Verifier(Matcher(), 4096)).body if dec.transformations else chat.body
        out = f"dispatched; AWS key in provider bytes={KEY.encode() in body}; email in bytes={MAIL.encode() in body}"
    print(f"{name:<55} -> {dec.disposition.value:<6} deciding_rules={dec.deciding_rules} | {out}")
