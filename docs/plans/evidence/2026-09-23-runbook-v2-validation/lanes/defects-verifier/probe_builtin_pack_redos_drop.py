"""Probe B3 (side finding): which control-plane built-in pack regex rules does the gateway
policy engine silently drop? policy_engine._compile_regex raises re.error for ReDoS-shaped
patterns (policy_engine.py:258-271) and every caller treats re.error as "no match"
(policy_engine.py:675-678, 756-759). Usage: <ROOT>.
"""
import importlib.util
import os
import re
import sys

ROOT = sys.argv[1]
import policy_engine as PE  # noqa: E402

assert PE.__file__.startswith(ROOT)
spec = importlib.util.spec_from_file_location(
    "builtin_packs_catalog", os.path.join(ROOT, "control/ai_mesh_control/policy/builtin_packs_catalog.py"))
BP = importlib.util.module_from_spec(spec)
sys.modules["builtin_packs_catalog"] = BP
spec.loader.exec_module(BP)
print("ROOT =", ROOT)
total = dropped = 0
for fam in BP.FAMILIES:
    for r in fam.rules:
        if r.rule_type != "regex":
            continue
        total += 1
        try:
            PE._compile_regex(r.regex)
            status = "compiles"
        except re.error as exc:
            dropped += 1
            status = f"DROPPED ({exc})"
        if status != "compiles" or fam.key == "command_injection":
            print(f"  {fam.key:<18} {r.name[:44]:<44} {status}")
print(f"regex rules in catalog: {total}; silently dropped by gateway _compile_regex: {dropped}")
# end-to-end: the dropped rule vs. a genuine injection inside backticks
narrow = next(r for f in BP.FAMILIES if f.key == "command_injection" for r in f.rules if "narrowed" in r.name)
entry = {"policy": {"id": 1, "code": "c", "name": "ci", "priority": 1},
         "rules": [{"id": 1, "name": narrow.name, "rule_type": "regex", "action": "block",
                    "condition": {"regex": narrow.regex, "field": "both"}}]}
atk = "run this: `cat /etc/passwd | sh`"
print("python re.search(narrowed, attack) =", bool(re.search(narrow.regex, atk)))
print("policy_engine.evaluate(attack, [narrowed-only bundle]).action =", repr(PE.evaluate(atk, "", [entry]).action))
