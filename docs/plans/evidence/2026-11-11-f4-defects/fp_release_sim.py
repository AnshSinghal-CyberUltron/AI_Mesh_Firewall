"""PR #115 round 3: does a RELEASE change the rollout fingerprint? (Reviewers A-D1, B-D1)

The reviewers stood in for a release with in-process patches. A release edits files, so this
copies ``control/ai_mesh_control`` to a temporary tree, applies one edit per case, and computes
``core.v2_plan_rollout.fingerprint()`` in a fresh process per tree (no database query is made:
the serializer's org-dependent fields return empty for an unsaved config).

Run from anywhere: ``python fp_release_sim.py <repo root> <settings dir>``, where the settings dir
holds a Django settings module named in DJANGO_SETTINGS_MODULE (the local pgvector shim here).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# argv names a management command so the apps' start-up guard starts none of the server's
# background jobs in these short processes (register R29).
PROG = (
    "import sys; sys.argv = ['manage.py', 'fp_release_sim']; import django; django.setup(); "
    "from core.v2_plan_rollout import fingerprint; print(fingerprint())"
)

# (case, file under ai_mesh_control, old text, new text, who raised it)
CASES = [
    ("customer default priority 200 -> 150 (an R24 answer)", "core/firewall_v2_publish.py",
     "    return 200\n", "    return 150\n", "B-D1"),
    ("customer rows fail_open", "core/firewall_v2_publish.py",
     '"on_unavailable": "fail_closed",\n        "threshold": None,\n        "priority": _priority(',
     '"on_unavailable": "fail_open",\n        "threshold": None,\n        "priority": _priority(',
     "B-D1"),
    ("monitor-posture org rows", "core/firewall_v2_map.py",
     '        if mode == "monitor":\n            ext["action"], ext["mode"] = "flag", "monitor"',
     '        if mode == "monitor":\n            ext["action"], ext["mode"] = "allow", "monitor"',
     "B-D1"),
    ("canary row action (an org in V2_CANARY_ORGS)", "core/firewall_v2_publish.py",
     '_platform_row("out_canary", "canary", "output.canary", "block", [])',
     '_platform_row("out_canary", "canary", "output.canary", "flag", [])', "A-D1"),
    ("FirewallConfig default tier2_fail_closed_enabled True -> False (never-saved orgs)",
     "core/models.py",
     "    tier2_fail_closed_enabled = models.BooleanField(\n        default=True,",
     "    tier2_fail_closed_enabled = models.BooleanField(\n        default=False,", "A-D1"),
    ("a new data migration in core", "core/migrations/0044_f4_sim_data_fix.py", None,
     "from django.db import migrations\n\n\nclass Migration(migrations.Migration):\n"
     "    dependencies = [(\"core\", \"0043_v2plandocument_publisher\")]\n"
     "    operations = []\n", "A-D1"),
    ("cost: a comment-only edit to a publisher module", "core/firewall_v2_map.py",
     "def drafts_for(", "# a comment\ndef drafts_for(", "author"),
    ("control: an edit outside the publisher (core/views.py comment)", "core/views.py",
     None, "\n# a comment\n", "author"),
]


def fingerprint(tree: Path, settings_dir: str, shared: str, seed: str = "0") -> str:
    env = {
        **os.environ,
        "PYTHONPATH": f"{settings_dir}{os.pathsep}{tree}{os.pathsep}{shared}",
        "PYTHONHASHSEED": seed,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    run = subprocess.run(
        [sys.executable, "-c", PROG], env=env, capture_output=True, text=True, check=False,
        cwd=tempfile.gettempdir(),
    )
    lines = run.stdout.strip().splitlines()
    if run.returncode != 0 or not lines:
        return f"ERROR rc={run.returncode} {run.stderr.strip().splitlines()[-1:]}"
    return lines[-1]


def main() -> None:
    root, settings_dir = Path(sys.argv[1]), sys.argv[2]
    source, shared = root / "control" / "ai_mesh_control", str(root / "shared")
    work = Path(tempfile.mkdtemp(prefix="f4-fp-sim-"))
    try:
        base_tree = work / "base"
        shutil.copytree(source, base_tree, ignore=shutil.ignore_patterns("__pycache__"))
        base = fingerprint(base_tree, settings_dir, shared)
        seeds = {fingerprint(base_tree, settings_dir, shared, seed) for seed in ("1", "2", "12345")}
        print(f"baseline {base[:16]} (PYTHONHASHSEED 0, 1, 2, 12345 give "
              f"{len(seeds | {base})} distinct value)")
        print("| change | raised by | fingerprint changed |")
        print("|---|---|---|")
        for case, rel, old, new, who in CASES:
            tree = work / "case"
            shutil.rmtree(tree, ignore_errors=True)
            shutil.copytree(base_tree, tree)
            path = tree / rel
            if old is None:
                path.write_text((path.read_text() if path.exists() else "") + new)
            else:
                text = path.read_text()
                assert text.count(old) == 1, (case, text.count(old))
                path.write_text(text.replace(old, new))
            got = fingerprint(tree, settings_dir, shared)
            print(f"| {case} | {who} | {got != base} ({got[:16]}) |")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
