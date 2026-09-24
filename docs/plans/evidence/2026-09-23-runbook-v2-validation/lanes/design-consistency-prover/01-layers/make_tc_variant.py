"""Rewrite V1 stubs: move chosen cross-layer imports under `if TYPE_CHECKING:`.

mode=minimal : move only annotation-only names (runtime-needed names stay at top level)
mode=all     : move every cross-layer name (to test whether the runtime still works)
"""
import re, sys, pathlib

root = pathlib.Path(sys.argv[1]) / "gateway_v2"
mode = sys.argv[2]
# file -> {import-module: (runtime_names, annotation_only_names)}
PLAN = {
 "detect/base.py":        {"gateway_v2.plan.model": ({"Category"}, {"ExecutionPlan"})},
 "resolve/decision.py":   {"gateway_v2.detect.base": (set(), {"Finding", "Span"})},
 "resolve/conflict.py":   {"gateway_v2.plan.model": (set(), {"Rule"})},
 "resolve/resolver.py":   {"gateway_v2.detect.base": ({"FindingStatus"}, {"Finding"}),
                           "gateway_v2.plan.model": ({"Action", "FailurePosture", "Mode"}, {"ExecutionPlan", "Rule"})},
 "dispatch/provider.py":  {"gateway_v2.resolve.decision": ({"DispatchAuthorization"}, set())},
 "dispatch/transform.py": {"gateway_v2.detect.base": (set(), {"Span"}),
                           "gateway_v2.resolve.decision": (set(), {"Decision", "Transformation"})},
 "dispatch/routing.py":   {"gateway_v2.plan.model": (set(), {"ExecutionPlan"})},
 "egress/stream.py":      {"gateway_v2.dispatch.provider": (set(), {"ProviderStream"}),
                           "gateway_v2.plan.model": ({"StreamingMode"}, {"ExecutionPlan"})},
 "egress/output_guard.py":{"gateway_v2.detect.base": (set(), {"Detector"}),
                           "gateway_v2.plan.model": (set(), {"ExecutionPlan"}),
                           "gateway_v2.resolve.decision": ({"Phase"}, {"Decision"}),
                           "gateway_v2.resolve.resolver": ({"resolve"}, set())},
 "audit/record.py":       {"gateway_v2.detect.base": (set(), {"Finding"}),
                           "gateway_v2.resolve.decision": (set(), {"Decision", "Phase"})},
}
for rel, mods in PLAN.items():
    path = root / rel
    src = path.read_text()
    tc_lines, keep_lines = [], []
    for mod, (runtime, annot) in mods.items():
        m = re.search(rf"^from {re.escape(mod)} import ([^\n]+)\n", src, re.M)
        assert m, (rel, mod)
        names = [n.strip() for n in m.group(1).split(",")]
        assert set(names) == runtime | annot, (rel, mod, names)
        top = sorted(runtime) if mode == "minimal" else []
        tc = sorted(annot) if mode == "minimal" else sorted(runtime | annot)
        new = ""
        if top:
            new += f"from {mod} import {', '.join(top)}\n"
        if tc:
            tc_lines.append(f"    from {mod} import {', '.join(tc)}\n")
        src = src[: m.start()] + new + src[m.end():]
    if tc_lines:
        src = src.replace("from __future__ import annotations\n",
                          "from __future__ import annotations\n\nfrom typing import TYPE_CHECKING\n", 1)
        # append the TYPE_CHECKING block after the last top-level import line
        lines = src.splitlines(keepends=True)
        last = max(i for i, l in enumerate(lines) if l.startswith(("import ", "from ")))
        lines.insert(last + 1, "\nif TYPE_CHECKING:\n" + "".join(tc_lines))
        src = "".join(lines)
    path.write_text(src)
pp = pathlib.Path(sys.argv[1]) / "pyproject.toml"
t = pp.read_text()
t = t.replace('[tool.importlinter]\nroot_packages = ["gateway_v2"]',
              '[tool.importlinter]\nroot_packages = ["gateway_v2"]\nexclude_type_checking_imports = true')
assert "exclude_type_checking_imports = true" in t
pp.write_text(t)
print("rewrote", len(PLAN), "files; mode =", mode)
