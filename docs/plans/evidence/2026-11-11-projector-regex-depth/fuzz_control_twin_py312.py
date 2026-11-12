"""The structured and token-soup fuzz against control's twin, on the control interpreter (3.12).

Run from the repo root: `python docs/plans/evidence/<dir>/fuzz_control_twin_py312.py <seed> <n>`.
"""
import ast
import pathlib
import random
import re
import sys
import warnings

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
source = (ROOT / "control/ai_mesh_control/policy/serializers.py").read_text()
keep = {"_INLINE_FLAGS_RE", "group_depth", "_class_end", "_skip_past"}
nodes = [n for n in ast.parse(source).body
         if (isinstance(n, ast.FunctionDef) and n.name in keep)
         or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in keep for t in n.targets))]
twin: dict = {"re": re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "control-twin", "exec"), twin)

fuzz = (HERE / "fuzz_structured.py").read_text()
helpers: dict = {"warnings": warnings, "random": random, "re": re}
exec(fuzz[fuzz.index("from re import"):fuzz.index("rng = random.Random")], helpers)
soup = (HERE / "fuzz_token_soup.py").read_text()
tokens: dict = {}
exec(soup[soup.index("TOKENS ="):soup.index("rng = random.Random")], tokens)

warnings.simplefilter("ignore")
seed, samples = int(sys.argv[1]), int(sys.argv[2])
for name, make in (("structured", lambda r: helpers["gen"](r)),
                   ("token-soup", lambda r: "".join(r.choice(tokens["TOKENS"])
                                                    for _ in range(r.randint(1, 40))))):
    rng = random.Random(seed)
    checked = under = 0
    for _ in range(samples):
        pattern = make(rng)
        try:
            tree = helpers["P"].parse(pattern)
        except (re.error, RecursionError, OverflowError):
            continue
        checked += 1
        if twin["group_depth"](pattern) < helpers["true_depth"](tree):
            under += 1
            print("UNDER", repr(pattern))
    print(f"python {sys.version.split()[0]} control twin {name} seed={seed}: "
          f"checked={checked} under_counts={under}")
