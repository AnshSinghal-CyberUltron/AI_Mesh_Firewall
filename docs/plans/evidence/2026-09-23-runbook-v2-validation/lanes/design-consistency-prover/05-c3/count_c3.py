import json, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
root = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall/tests/detection_corpus")
rows = [json.loads(l) for fn in ("malicious.jsonl", "benign.jsonl") for l in (root / fn).read_text().splitlines() if l.strip()]
norm = lambda t: re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t).lower()).strip(" .?!")
print(f"total rows {len(rows)}; labels {dict(Counter(r['label'] for r in rows))}")
print(f"distinct ids {len({r['id'] for r in rows})}; distinct texts {len({r['text'] for r in rows})}; distinct normalized {len({norm(r['text']) for r in rows})}")
fam = defaultdict(list)
for r in rows: fam[(r["label"], r["family"])].append(r)
print(f"\n{'label':9s} {'family':18s} {'n':>4s} {'uniq':>4s} {'eval':>4s} {'train':>5s} {'origin (provenance prefix)':30s} {'non-ascii':>9s} {'max_chars':>9s}")
for (lab, f), items in sorted(fam.items()):
    prov = Counter(i["provenance"].split(":")[0] for i in items)
    split = Counter(i["split"] for i in items)
    nonascii = sum(any(ord(c) > 127 for c in i["text"]) for i in items)
    print(f"{lab:9s} {f:18s} {len(items):4d} {len({norm(i['text']) for i in items}):4d} {split.get('eval',0):4d} {split.get('train',0):5d} {str(dict(prov)):30s} {nonascii:9d} {max(len(i['text']) for i in items):9d}")
mal = [r for r in rows if r["label"] == "malicious"]
print(f"\nmalicious total {len(mal)} across {len({r['family'] for r in mal})} families; "
      f"pattern-derived (provenance marker = a v1 ATTACK_PATTERNS family) = {sum(r['family'] != 'paraphrase' for r in mal)}; paraphrase = {sum(r['family']=='paraphrase' for r in mal)}")
print("benign total", sum(r['label']=='benign' for r in rows), "developer_traffic", len(fam[('benign','developer_traffic')]))
print("families with PII/secret/credential labels:", [f for (_, f) in fam if re.search('pii|secret|cred', f)])
print("rows with non-empty fake_fixtures:", sum(bool(r["fake_fixtures"]) for r in rows))
print("label fields available per row:", sorted(rows[0].keys()), "(no spans / expected action / org policy / provider-byte outcome)")
print("texts > 2000 chars (long-input/windowing cases):", sum(len(r["text"]) > 2000 for r in rows), "| longest:", max(len(r["text"]) for r in rows))
print("texts with base64/hex/url-encoding shapes:", sum(bool(re.search(r"[A-Za-z0-9+/]{24,}={0,2}|%[0-9A-Fa-f]{2}|\\\\x[0-9a-f]{2}", r["text"])) for r in rows))
