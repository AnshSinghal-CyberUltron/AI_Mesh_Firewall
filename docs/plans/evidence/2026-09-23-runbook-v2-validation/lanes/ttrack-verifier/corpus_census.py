"""Census of tests/detection_corpus vs runbook T04/C3 requirements. Read-only."""
import collections, hashlib, json, subprocess, sys
from pathlib import Path
REPO = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
C = REPO / "tests/detection_corpus"
def load(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
mal, ben = load(C / "malicious.jsonl"), load(C / "benign.jsonl")
out = {}
for name, rows in (("malicious", mal), ("benign", ben)):
    fam = collections.Counter(r["family"] for r in rows)
    split = collections.Counter(r.get("split") for r in rows)
    fam_split = collections.Counter((r["family"], r.get("split")) for r in rows)
    labels = collections.Counter(r.get("label") for r in rows)
    keys = collections.Counter(k for r in rows for k in r)
    out[name] = {
        "rows": len(rows), "labels": dict(labels), "families": dict(fam),
        "splits": dict(split),
        "family_x_split": {f"{a}|{b}": n for (a, b), n in sorted(fam_split.items())},
        "record_keys": dict(keys),
        "has_expected_action_or_span_labels": any(k in keys for k in ("expected_action","spans","expected_spans","org_policy","provider_bytes","final_action")),
        "has_language_field": any(k in keys for k in ("lang","language")),
    }
out["families_json"] = json.loads((C / "families.json").read_text())
out["attack_family_count_nonempty"] = sum(1 for n in out["malicious"]["families"].values() if n > 0)
out["sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (C/"malicious.jsonl", C/"benign.jsonl")}
# any committed frozen held-out hash / manifest?
g = subprocess.run(["git","-C",str(REPO),"grep","-n","-I","-E","held.?out|heldout|corpus_hash|corpus_sha|frozen","--","tests/detection_corpus","gateway_v2/contracts","scripts/detection","docs/plans/evidence"],capture_output=True,text=True)
out["git_grep_heldout_frozen_hash_hits"] = g.stdout.splitlines()[:60]
# log of corpus files
lg = subprocess.run(["git","-C",str(REPO),"log","--format=%h %ad %s","--date=iso","--","tests/detection_corpus/malicious.jsonl","tests/detection_corpus/benign.jsonl"],capture_output=True,text=True)
out["git_log_corpus_files"] = lg.stdout.splitlines()
json.dump(out, sys.stdout, indent=2)
