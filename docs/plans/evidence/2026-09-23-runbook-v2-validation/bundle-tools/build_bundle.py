"""Assemble the runbook-v2 validation evidence bundle inside the repo.

python3 build_bundle.py plan    -> print what would be copied/excluded (sizes), write nothing
python3 build_bundle.py build   -> copy into DEST, write MANIFEST.sha256 + EXCLUDED.tsv
Rules: code, specs, summaries and per-request raw data for the runs that define published numbers are copied;
models, virtualenvs, repo copies, generated test keys, binaries and bulk raw data are listed (path, bytes, sha256,
reason) in EXCLUDED.tsv and stay on the controller at their recorded path.
"""
import hashlib
import os
import shutil
import subprocess
import sys

SP = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad"
RAW = "/home/contact_cyberultron_com/rv-evidence-raw"
HRAW = "/home/contact_cyberultron_com/rv-harness-evidence"
REPO = "/home/contact_cyberultron_com/AI_Mesh_Firewall"
DEST = f"{REPO}/docs/plans/evidence/2026-09-23-runbook-v2-validation"

SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "models", "bin", "build", "a_oldcommits", "ci_sim", "gw2_pristine", "secret_repo", "stack", "vendor",
             "rvproto-frozen1", "live-src-1720", "live-src-1259", "bench-src", "pycache", "bl_copy", "head_copy"}
SKIP_PREFIX = ("fulltree_", "mut_")
SKIP_SUFFIX = (".pem", ".key", ".so", ".whl", ".onnx", ".engine", ".plan", ".safetensors", ".pt", ".pyc", ".db", ".sqlite",
               ".lock")
MAX_FILE = 20 * 1024 * 1024       # larger files are listed, not copied
COMPRESS_OVER = 256 * 1024          # text files above this are stored as .zst
FLAGGED = set(l.strip() for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "flagged_paths.txt")) if l.strip())
RAW_NAMES = ("requests.jsonl", "records.jsonl", "lag_samples.json", "gateway.log", "events.jsonl")
NOT_PRESERVED = ("/.venv/", "/venv/", "/node_modules/", "/__pycache__/", "/.git/", "/secret_repo/", "/pycache/")
TEXT_SUFFIX = (".jsonl", ".json", ".log", ".txt", ".md", ".csv", ".tsv", ".out", ".html", ".xml", ".prof")

# per-request raw data kept in git: the runs behind every published number (rest listed in EXCLUDED.tsv)
RAW_KEEP = {
    "proto-bench-unit/runs": None,     # filled at build time from KEEP_RUNS_UNIT
    "proto-bench-fleet/runs": None,
}
KEEP_RUNS_UNIT = ["p22-078"]   # small runs behind published numbers; the rest live in GCS
KEEP_RUNS_FLEET = []
GCS = "gs://ai-mesh-firewall-rv-evidence-20260923"

# sources that are copied whole (after the generic filters)
TREES = [
    (f"{SP}/v21", "runbook", "v2.1 build inputs + outputs"),
    (f"{SP}/runbook", "runbook/v2-source", "v2 docx extracted to markdown"),
    (f"{SP}/reports", "report", "findings ledger"),
    (f"{SP}/report", "report/page", "HTML verdict page + generator"),
    (f"{SP}/harness", "harness", "measurement harness source"),
    (f"{SP}/rvproto", "rvproto", "throwaway v2 prototype (rvproto-frozen-1 + later fixes)"),
    (f"{SP}/bundle", "bundle-tools", "this script"),
]
SPEC_FILES = ["CONTEXT.md", "GCP.md", "HARNESS_SPEC.md", "PROTO_SPEC.md"]
EXCLUDE_TOP = {"gcp", "models", "baseline-2a657fad", "tools", "rvproto-export", "an-test"}


def sha256(p, buf=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def is_elf(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def walk(src):
    for root, dirs, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        keep = []
        for d in dirs:
            if d in SKIP_DIRS or d.startswith(SKIP_PREFIX):
                yield ("xdir", os.path.join(root, d), f"excluded directory class '{d}'")
            else:
                keep.append(d)
        dirs[:] = keep
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                continue
            yield ("file", p, rel_root)


def classify(p):
    n = os.path.basename(p)
    if n.startswith(".env") or n.endswith(SKIP_SUFFIX) or "ed25519" in n or n in ("token", "credentials.json"):
        return "exclude", "credential/binary/model file class"
    sz = os.path.getsize(p)
    if sz > MAX_FILE:
        return "exclude", f"larger than {MAX_FILE >> 20} MiB"
    if sz > COMPRESS_OVER and n.endswith(TEXT_SUFFIX):
        return "compress", ""
    return "copy", ""


def plan():
    ops, excluded = [], []
    for src, dst, _why in TREES:
        for kind, p, extra in walk(src):
            if kind == "xdir":
                excluded.append((p, extra))
                continue
            rel = os.path.relpath(p, src)
            if dst == "rvproto" and rel.startswith("models"):
                excluded.append((p, "model weights"))
                continue
            act, why = classify(p)
            if act == "exclude":
                excluded.append((p, why))
            else:
                ops.append((act, p, f"{dst}/{rel}"))
    for f in SPEC_FILES:
        ops.append(("copy", f"{SP}/{f}", f"specs/{f}"))
    ops.append(("copy", f"{SP}/bundle/README.bundle.md", "README.md"))
    for lane in sorted(os.listdir(f"{SP}/evidence")):
        for kind, p, extra in walk(f"{SP}/evidence/{lane}"):
            if kind == "xdir":
                excluded.append((p, extra))
                continue
            rel = os.path.relpath(p, f"{SP}/evidence/{lane}")
            if f"{lane}/{rel}" in FLAGGED:
                excluded.append((p, "credential-shaped content (test fixture / repo copy) kept out of git"))
                continue
            bn = os.path.basename(p)
            if (".jsonl" in bn and os.path.getsize(p) > 256 * 1024) or bn.endswith((".tar.gz", ".tgz")) or is_elf(p):
                excluded.append((p, "per-request records / archive / binary (full copy in GCS)"))
                continue
            if os.path.basename(p).startswith(RAW_NAMES) or "/raw/" in "/" + rel:
                excluded.append((p, "per-request raw record / raw measurement page (full copy in GCS)"))
                continue
            act, why = classify(p)
            if act == "exclude":
                excluded.append((p, why))
            else:
                ops.append((act, p, f"lanes/{lane}/{rel}"))
    for sub, keep in (("proto-bench-unit/runs", KEEP_RUNS_UNIT), ("proto-bench-fleet/runs", KEEP_RUNS_FLEET)):
        base = f"{RAW}/{sub}"
        for run in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            for root, _d, files in os.walk(f"{base}/{run}"):
                for f in files:
                    p = os.path.join(root, f)
                    if run in keep and os.path.getsize(p) <= MAX_FILE * 2:
                        ops.append(("copy", p, f"raw/{sub}/{os.path.relpath(p, base)}"))
                    else:
                        excluded.append((p, "bulk raw data (not a headline run)" if run not in keep else "too large"))
    for top in (f"{RAW}/micro-claims", f"{RAW}/proto-bench-split", HRAW):
        for root, _d, files in os.walk(top):
            for f in files:
                excluded.append((os.path.join(root, f), "bulk raw data (validation/micro-benchmark per-request records)"))
    for top in sorted(EXCLUDE_TOP):
        p = f"{SP}/{top}"
        if os.path.exists(p):
            excluded.append((p, "controller-only (keys, model weights, repo snapshot or scratch)"))
    for f in ("rvproto-unit.tar.gz", "rvproto-code.tar.gz", "v1-src-52a584e9.tgz", "worst300.jsonl", "sp.test", "mem.prof"):
        if os.path.exists(f"{SP}/{f}"):
            excluded.append((f"{SP}/{f}", "archive/scratch duplicate of bundled content"))
    return ops, excluded


def gs_uri(p):
    home = "/home/contact_cyberultron_com/"
    if p.startswith(RAW + "/"):
        return GCS + "/rv-evidence-raw/" + p[len(RAW) + 1:]
    if p.startswith(HRAW + "/"):
        return GCS + "/rv-harness-evidence/" + p[len(HRAW) + 1:]
    if p.startswith(SP + "/evidence/"):
        q = p + ("/" if os.path.isdir(p) else "")
        if any(x in q for x in NOT_PRESERVED) or q.endswith((".pem", ".key")):
            return "(not preserved: generated/reproducible or test key material)"
        return GCS + "/sp-evidence/" + p[len(SP) + len("/evidence/"):]
    return "(controller only)"


def size_of(p):
    if os.path.isdir(p):
        return int(subprocess.check_output(["du", "-sb", p]).split()[0])
    return os.path.getsize(p)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    ops, excluded = plan()
    copy_bytes = sum(os.path.getsize(p) for _a, p, _d in ops)
    print(f"copy/compress ops: {len(ops)} files, {copy_bytes / 1048576:.1f} MiB before compression")
    print(f"excluded entries: {len(excluded)}")
    if mode == "plan":
        by = {}
        for _a, p, d in ops:
            k = d.split("/")[0] + "/" + (d.split("/")[1] if "/" in d else "")
            by[k] = by.get(k, 0) + os.path.getsize(p)
        for k, v in sorted(by.items(), key=lambda x: -x[1])[:30]:
            print(f"  {v / 1048576:8.1f} MiB  {k}")
        return
    if os.path.exists(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)
    for act, p, d in ops:
        out = os.path.join(DEST, d)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if act == "compress":
            subprocess.check_call(["zstd", "-q", "-19", "-T8", p, "-o", out + ".zst"])
        else:
            shutil.copy2(p, out)
    with open(f"{DEST}/EXCLUDED.tsv", "w") as f:
        f.write("path_on_controller\tbytes\tsha256\tgcs_copy\treason\n")
        for p, why in sorted(excluded):
            if os.path.isdir(p):
                f.write(f"{p}/\t{size_of(p)}\t(directory)\t{gs_uri(p)}\t{why}\n")
            else:
                f.write(f"{p}\t{os.path.getsize(p)}\t{sha256(p)}\t{gs_uri(p)}\t{why}\n")
    subprocess.check_call(["zstd", "-q", "-19", "--rm", f"{DEST}/EXCLUDED.tsv", "-o", f"{DEST}/EXCLUDED.tsv.zst"])
    files = []
    for root, _d, fs in os.walk(DEST):
        for fn in fs:
            if fn != "MANIFEST.sha256":
                files.append(os.path.join(root, fn))
    with open(f"{DEST}/MANIFEST.sha256", "w") as f:
        for p in sorted(files):
            f.write(f"{sha256(p)}  {os.path.relpath(p, DEST)}\n")
    total = sum(os.path.getsize(p) for p in files)
    print(f"bundle: {len(files)} files, {total / 1048576:.1f} MiB at {DEST}")


if __name__ == "__main__":
    main()
