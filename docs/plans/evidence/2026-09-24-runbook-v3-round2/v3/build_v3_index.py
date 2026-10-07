#!/usr/bin/env python3
"""Build the round-2 evidence index committed next to runbook v3.

Copies an explicit allow-list of small curated files from the round-2 scratchpad into
docs/plans/evidence/2026-09-24-runbook-v3-round2/, filters the teardown log, compresses
text files larger than 256 KiB with zstd (as the round-1 bundle does), refuses any path
that matches a credential pattern, and writes MANIFEST.sha256. Secret scanning is a
separate step (bundle-tools/secret_scan.py from the round-1 bundle) run on the result.
"""
import fnmatch
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SP = Path("/home/contact_cyberultron_com/rv-evidence-raw/round2-scratchpad-20260924")
V3 = SP / "v3"
REPO = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
OUT = REPO / "docs/plans/evidence/2026-09-24-runbook-v3-round2"
ZST_OVER = 256 * 1024

# Never copied, whatever the allow-list says (credential material and model weights).
DENY = ["*/rv_ed25519*", "*/secrets/*", "*.env", "*kubeconfig*", "*pgpass*", "*.pg_ip",
        "*keys-50k.txt", "*/auth-*", "*.pem", "*/models/*", "*.key"]

# (destination dir, source glob relative to SP)
ALLOW = [
    ("report", "reports/FINDINGS_LEDGER.md"),
    ("report", "CONTROLLER_BULLETIN.md"),
    ("report", "ROUND2_SPEC.md"),
    ("v3", "v3/v3_notes.md"),
    ("v3", "v3/rc3_tracker.md"),
    ("v3", "v3/part0_v3.md"),
    ("v3", "v3/assemble_v3.py"),
    ("v3", "v3/build_v3_index.py"),
    ("prototype", "evidence/r2-impl/rc2/rvproto2-rc2.tar.gz*"),
    ("prototype", "evidence/r2-impl/rc1/rvproto2-rc1.tar.gz*"),
    ("prototype", "rc2_manifest.txt"),
    ("prototype", "rc1_manifest.txt"),
    ("prototype", "evidence/r2-impl/IMAGES"),
    ("prototype", "evidence/r2-impl/RC2_SPEC.md"),
    ("prototype", "evidence/r2-impl/USAGE.md"),
    ("patches", "evidence/r2-impl/patches/*"),
    ("teardown", "evidence/controller-r2/teardown/*.txt"),
    ("teardown", "evidence/controller-r2/kv_trim_shared_valkey.txt"),
]
# Key result tables, kept at their evidence/ paths so §0.2 citations resolve inside the index.
TABLES = [
    "evidence/r2-edge/tables/*",
    "evidence/r2-fleet/tables/*",
    "evidence/r2-unit/runs/*/overload_summary.md",
    "evidence/r2-unit/phaseF-m1/*.txt",
    "evidence/r2-state/runs/rc2-*/analysis/verdict.json",
    "evidence/r2-fix-c36m/rc3/functional/RESULTS.txt",
    "evidence/r2-scale/levels/*.analysis.out",
    "evidence/reviewer-*/r2-pass1.md",
    "evidence/reviewer-hidden-failures/r2p1/p04_holdback_rc2.out",
    "evidence/r2-alt/alternatives.json",
    "evidence/r2-alt/gpu_sweep.json",
    "evidence/r2-alt/survey/table.md",
    "evidence/r2-infra/l4-pool.jsonl",
    "evidence/r2-infra/raw/drills/*.analysis.*",
    "evidence/r2-infra/raw/valkey_failover_api_recheck.txt",
    "evidence/r2-mig/notes/actions.log",
    "evidence/r2-mig/notes/drain-p1.txt",
    "evidence/r2-mig/notes/timeline-crosscheck.txt",
    "evidence/r2-gke/notes/steady-table-k1x1.md",
    "evidence/r2-chaos/notes/decisions.txt",
]


def denied(rel: str) -> bool:
    return any(fnmatch.fnmatch("/" + rel, pat) or fnmatch.fnmatch(rel, pat) for pat in DENY)


def put(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.stat().st_size > ZST_OVER and not src.name.endswith((".gz", ".zst")):
        dst = dst.with_name(dst.name + ".zst")
        subprocess.run(["zstd", "-q", "-19", "-f", str(src), "-o", str(dst)], check=True)
    else:
        shutil.copy2(src, dst)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    n = 0
    for dest, pattern in ALLOW:
        hits = sorted(glob.glob(str(SP / pattern)))
        if not hits:
            sys.exit(f"allow-list entry matched nothing: {pattern}")
        for h in hits:
            rel = os.path.relpath(h, SP)
            if denied(rel) or not os.path.isfile(h):
                continue
            put(Path(h), OUT / dest / Path(h).name)
            n += 1
    for pattern in TABLES:
        hits = sorted(glob.glob(str(SP / pattern)))
        if not hits:
            sys.exit(f"table entry matched nothing: {pattern}")
        for h in hits:
            rel = os.path.relpath(h, SP)
            if denied(rel) or not os.path.isfile(h) or os.path.getsize(h) == 0:
                continue
            put(Path(h), OUT / rel)
            n += 1

    # The teardown log is 8.8 MB of per-object bucket deletions; keep every other line.
    log = SP / "evidence/controller-r2/teardown/teardown.log"
    keep = [ln for ln in log.read_text(errors="replace").splitlines()
            if not ln.startswith("Removing gs://") and not re.fullmatch(r"\.+", ln.strip())]
    # Google API errors carry a support "Help Token"; it is not a credential, but it has no use here either.
    summary = re.sub(r"(Help Token:\s*)\S+", r"\1<redacted>", "\n".join(keep) + "\n")
    (OUT / "teardown/teardown_summary.log").write_text(summary)
    n += 1
    live = V3 / "live_inventory_20260924T1833Z.txt"
    if live.exists():
        shutil.copy2(live, OUT / "teardown" / live.name)
        n += 1

    shutil.copy2(V3 / "README.index.md", OUT / "README.md")
    n += 1

    lines = []
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(OUT)}")
    (OUT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"{n} files copied, {len(lines)} in manifest, {total / 1e6:.2f} MB total -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
