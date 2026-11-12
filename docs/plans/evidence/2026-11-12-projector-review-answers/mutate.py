"""PR #117 review answers: one mutant at a time in a scratch worktree (argv[1]); git checkout restores."""
import pathlib
import subprocess
import sys

WT = pathlib.Path(sys.argv[1])
PY = "/home/claude/work/venv-ci/bin/python"
PROJ = "gateway_v2/state_control/projector.py"
SVC = "gateway_v2/state_control/projector_service.py"
COND = "gateway_v2/gateway_v2/detect/conditions.py"
TESTS = [
    "tests/state_control/test_projector_body_depth.py",
    "tests/state_control/test_projector_notes.py",
    "tests/state_control/test_projector_poisoned_tenant.py",
    "tests/detect/test_condition_group_depth.py",
    "tests/plan/test_rewrite_template_refused_condition.py",
]
MUTANTS = {
    "G1 depth pre-scan removed": [(PROJ,
        '    if body_depth(raw) > MAX_PLAN_BODY_DEPTH:\n'
        '        return None, 0, f"body nests deeper than {MAX_PLAN_BODY_DEPTH} levels"\n', '')],
    "G2 off-by-one (>=)": [(PROJ, "if body_depth(raw) > MAX_PLAN_BODY_DEPTH:",
                            "if body_depth(raw) >= MAX_PLAN_BODY_DEPTH:")],
    "G3 MAX_PLAN_BODY_DEPTH = 64": [(PROJ, "MAX_PLAN_BODY_DEPTH = 32\n", "MAX_PLAN_BODY_DEPTH = 64\n")],
    "G4 escape pass dropped": [(PROJ, '_JSON_STRING.sub("", _JSON_ESCAPE.sub("", raw))',
                                '_JSON_STRING.sub("", raw)')],
    "G5 string pass dropped": [(PROJ, '_JSON_NOT_BRACKET.sub("", _JSON_STRING.sub("", _JSON_ESCAPE.sub("", raw)))',
                                '_JSON_NOT_BRACKET.sub("", _JSON_ESCAPE.sub("", raw))')],
    "G6 catch-all logs exception text": [(PROJ,
        'return None, 0, f"{type(exc).__name__}: unanticipated fault while validating"',
        'return None, 0, f"{type(exc).__name__}: {exc}"[:200]')],
    "G7 CompileError logs its text": [(PROJ,
        '    except CompileError:\n        return None, 0, "CompileError: the plan does not compile"',
        '    except CompileError as exc:\n        return None, 0, f"CompileError: {exc}"[:200]')],
    "G8 claimed org quoted": [(PROJ, 'return None, 0, "body claims another org"',
                               'return None, 0, f"body claims org {document.org_id!r}"')],
    "G9 canonical-form error text": [(PROJ,
        'f"{type(exc).__name__}: no canonical form (NaN, Infinity, lone surrogate)"',
        'f"{type(exc).__name__}: {exc}"[:200]')],
    "G10 JSON error text": [(PROJ, 'f"{type(exc).__name__}: body is not valid JSON"',
                             'f"{type(exc).__name__}: {exc}"[:200]')],
    "G11 refused-conditions note removed (N1)": [(PROJ, "            if refusals:\n", "            if False:\n")],
    "G12 failed round logged as hand-over (N3)": [(SVC,
        "        if summary is None:\n", "        if summary is None and False:\n"),
        (SVC, "        leading = summary.leader\n", "        leading = summary is not None and summary.leader\n")],
    "G13 MAX_GROUP_DEPTH = 32 (gateway)": [(COND, "MAX_GROUP_DEPTH = 16\n", "MAX_GROUP_DEPTH = 32\n")],
}


def run() -> str:
    out = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *TESTS],
        cwd=WT / "gateway_v2", capture_output=True, text=True,
        env={"PYTHONPATH": f"{WT}/gateway_v2:{WT}/shared", "PATH": "/usr/bin:/bin"},
    ).stdout.strip().splitlines()
    tail = out[-1] if out else "no output"
    failed = sorted({line.split("::")[0].split("/")[-1] + "::" + line.split("::")[1].split(" ")[0].split("[")[0]
                     for line in out if line.startswith("FAILED ")})
    return tail + ("" if not failed else "\n      killed by: " + "; ".join(failed[:6]))


def restore() -> None:
    subprocess.run(["git", "-C", str(WT), "checkout", "-q", "--", "."], check=True)
    assert not subprocess.run(["git", "-C", str(WT), "status", "--porcelain"],
                              capture_output=True, text=True).stdout.strip()


restore()
print("baseline:", run())
for name, edits in MUTANTS.items():
    for rel, old, new in edits:
        path = WT / rel
        text = path.read_text()
        assert text.count(old) == 1, (name, old)
        path.write_text(text.replace(old, new))
    print(f"{name}: {run()}")
    restore()
print("restored:", run())
