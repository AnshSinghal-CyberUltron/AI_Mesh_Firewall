import re
from policy.serializers import validate_condition, validate_redaction_config
from policy import engine, redaction
from core.firewall_v2_publish import _matches_own_pattern

deep = "(" * 499 + "a" + ")" * 499
def show(label, fn):
    re.purge()
    try:
        print(f"{label}: returned {fn()!r}"[:200])
    except BaseException as exc:
        print(f"{label}: RAISED {type(exc).__name__}: {str(exc)[:80]}")

show("validate_condition({'regex': 499-deep})", lambda: validate_condition({"regex": deep}))
show("validate_redaction_config({'regex': 499-deep})", lambda: validate_redaction_config({"regex": deep}))
show("validate_condition({'regex': 'a{4294967296}'})", lambda: validate_condition({"regex": "a{4294967296}"}))
show("validate_condition({'regex': 'a(b'}) [re.error baseline]", lambda: validate_condition({"regex": "a(b"}))
show("redaction._smart_substitute(text, 499-deep)", lambda: redaction._smart_substitute("aaa", deep, "X"))
show("redaction._smart_substitute(text, 'a(b') [re.error baseline]", lambda: redaction._smart_substitute("secret a(b", "a(b", "X"))
show("firewall_v2_publish._matches_own_pattern(499-deep)", lambda: _matches_own_pattern({"regex": deep}, "x"))
class R: id = 7; condition = {"regex": deep}; rule_type = "regex"
from policy.models import Rule as PRule
show("engine._evaluate_rule(block rule, 499-deep)", lambda: engine._evaluate_rule(PRule(id=7, rule_type="regex", action="block", condition={"regex": deep}), {"prompt": "aaa"}))
show("engine._evaluate_rule(block rule, 'a(b') [re.error baseline]", lambda: engine._evaluate_rule(PRule(id=8, rule_type="regex", action="block", condition={"regex": "a(b"}), {"prompt": "a(b"}))
