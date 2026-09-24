"""Attribute proto-builder's per-request PG2 FPR@0.5 (J: headline 1.92%/2.06%, worst 2.55%/3.0%) to benign
corpus snippets that PG2 flags on their own. Joins harness corpus entries (messages) with proto-builder's
per-request scores (guard-fpr/<profile>-<M>.requests.jsonl). Snippet self-scores = guard-bench ref_scores.json."""
import json, sys
SP, REPO = sys.argv[1], sys.argv[2]
out = {}
for M in ("22M", "86M"):
    ref = json.load(open(f"{SP}/evidence/guard-bench/raw/E/Llama-Prompt-Guard-2-{M}/ref_scores.json"))
    ben = [json.loads(l) for l in open(f"{REPO}/tests/detection_corpus/benign.jsonl") if l.strip()]
    hot = [b["text"] for b in ben if ref[b["id"]]["p1"] >= 0.5]
    warm = [b["text"] for b in ben if 0.2 <= ref[b["id"]]["p1"] < 0.5]
    for prof in ("headline", "worst", "correctness"):
        corp = {}
        for l in open(f"{SP}/harness/corpora/{prof}-{M}.jsonl"):
            e = json.loads(l); corp[e["id"]] = e
        rows = [json.loads(l) for l in open(f"{SP}/evidence/proto-builder/guard-fpr/{prof}-{M}.requests.jsonl")]
        rows = [r for r in rows if r["class"] == "benign"]
        def txt(r):
            return "\n".join(m["content"] for m in corp[r["id"]]["messages"] if isinstance(m.get("content"), str))
        fl = [r for r in rows if r["max_score"] >= 0.5]
        has_hot = [any(h in txt(r) for h in hot) for r in rows]
        n_hot = sum(has_hot)
        fl_hot = sum(1 for r, h in zip(rows, has_hot) if h and r["max_score"] >= 0.5)
        fl_cold = sum(1 for r, h in zip(rows, has_hot) if not h and r["max_score"] >= 0.5)
        out[f"{prof}-{M}"] = {"benign_requests": len(rows), "fpr@0.5": round(len(fl) / len(rows), 5),
                              "self_flagged_snippets": hot,
                              "requests_containing_self_flagged": n_hot,
                              "flagged_among_containing": fl_hot,
                              "fpr_among_containing": round(fl_hot / n_hot, 4) if n_hot else None,
                              "flagged_among_not_containing": fl_cold,
                              "fpr_among_not_containing": round(fl_cold / (len(rows) - n_hot), 5)}
json.dump(out, open(f"{sys.argv[3]}", "w"), indent=1)
for k, v in out.items():
    print(k, {kk: vv for kk, vv in v.items() if kk != "self_flagged_snippets"})
print("22M self-flagged snippets:", out["headline-22M"]["self_flagged_snippets"])
print("86M self-flagged snippets:", out["headline-86M"]["self_flagged_snippets"])
