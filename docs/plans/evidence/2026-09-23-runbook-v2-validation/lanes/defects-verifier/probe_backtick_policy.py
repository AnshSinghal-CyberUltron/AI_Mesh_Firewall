"""Probe B2 (side finding): the control-plane 'NARROWED' command_injection built-in pack,
evaluated by the REAL gateway policy engine (policy_engine.evaluate) as it would be once an
operator enables the family (it is seeded enabled=False — builtin_packs_seed.py:9-15,97).

The bundle entry is hand-built in the shape documented at policy_engine.py:704-707
({"policy": {...}, "rules": [{rule_type, condition{regex, field}, action}]}), from the
catalog's own rule objects (builtin_packs_catalog.py:205-228). Usage: <ROOT>.
"""
import importlib.util
import os
import sys

ROOT = sys.argv[1]
import policy_engine as PE  # noqa: E402

assert PE.__file__.startswith(ROOT)
spec = importlib.util.spec_from_file_location(
    "builtin_packs_catalog", os.path.join(ROOT, "control/ai_mesh_control/policy/builtin_packs_catalog.py"))
BP = importlib.util.module_from_spec(spec)
sys.modules["builtin_packs_catalog"] = BP
spec.loader.exec_module(BP)
fam = next(f for f in BP.FAMILIES if f.key == "command_injection")
entry = {
    "policy": {"id": 1, "code": "builtin.command_injection", "name": fam.name, "priority": 100,
               "category": fam.category},
    "rules": [
        {"id": i + 1, "name": r.name, "rule_type": r.rule_type, "action": r.action,
         "condition": {"regex": r.regex, "field": r.field_scope}}
        for i, r in enumerate(fam.rules)
    ],
}
PROMPTS = [
    "What does `git rebase -i` do?",
    "Why does `list.sort()` return None in Python?",
    "How do I read `process.env.NODE_ENV` inside a Next.js API route?",
    "What's the difference between `String` and `&str` in Rust?",
    "My Dockerfile runs `npm ci && npm run build` but the image is 2 GB. How can I slim it down?",
    "Should I use `map` or `forEach` here?",
    "Is `for (int i = 0; i < n; i++)` idiomatic in modern C++?",
]
print("ROOT =", ROOT)
blocked = 0
for p in PROMPTS:
    r = PE.evaluate(p, "", [entry])
    blocked += r.action == "block"
    print(f"policy_engine.evaluate -> action={r.action!r:<8} rules={r.matched_rule_names}  prompt={p!r}")
print(f"blocked {blocked}/{len(PROMPTS)} (first 5 are the P8 benign set)")
