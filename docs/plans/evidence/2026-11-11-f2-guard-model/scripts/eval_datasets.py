"""L36-2: recall and false-positive rate of PG2-22M fp32 ONNX on public, licence-compatible data.

  flock /home/claude/work/.heavy.lock nice -n 10 .f2-scratch/venv/bin/python -I eval_datasets.py

Every input is windowed exactly as the plan proposes (tokenize once with the shipped tokenizer,
[CLS]+510+[SEP] rows, overlap 64, request score = MAX over windows). Downloaded files are read as
data only (parquet/csv/json). Outputs (no dataset text is written):
  results/eval_rows.csv      one row per item: dataset, index, label, language, tokens, windows,
                             score, argmax window
  results/eval_summary.json  per-dataset confusion matrices and Wilson 95% intervals at a grid of
                             thresholds, FPR by window count, per-language rows, the planted
                             (long-document) injection test for overlap 0/64/128, and the chosen
                             threshold with its justification inputs.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pyarrow.parquet as pq
from f2common import (
    EVIDENCE_DIR,
    OUT_DIR,
    load_tokenizer,
    make_session,
    p_malicious,
    plan_windows,
    run_rows,
)

REPO = EVIDENCE_DIR.parents[3]
DS = OUT_DIR / "datasets"
GRID = (0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995)
SEED = 20261111
#: Rows needing more windows than this are the production stage's UNAVAILABLE(over_length); they are
#: counted per dataset and excluded from the recall/FPR denominators (plan default max_windows=16,
#: 510 + 15 x 446 = 7,200 tokens).
MAX_WINDOWS = 16
#: Seeded sample sizes. CPU fp32 costs ~0.48 s of CPU per full 512-position window on this host
#: (results/bench_window.json), so the long-prompt sets are sampled to keep the run near 20 min CPU.
SAMPLE = {"jackhhao:jailbreak": 150, "inthewild:jailbreak": 150, "inthewild:regular": 400,
          "dolly:instruction": 1500, "dolly:long_context": 120, "planted_pairs": 60}
csv.field_size_limit(1 << 30)

STOP = {
    "en": {"the", "and", "you", "your", "all", "previous", "instructions", "is", "of", "to", "what"},
    "de": {"und", "die", "der", "das", "ich", "nicht", "sie", "alle", "anweisungen", "ist", "du"},
    "fr": {"les", "des", "vous", "et", "le", "la", "est", "toutes", "instructions", "votre", "tu"},
    "es": {"las", "los", "y", "el", "la", "es", "todas", "instrucciones", "tus", "que", "por"},
    "it": {"il", "di", "che", "e", "tutte", "le", "istruzioni", "sono", "tuo", "per", "non"},
    "pt": {"o", "os", "as", "e", "que", "todas", "instruções", "seu", "não", "para", "você"},
}


def guess_lang(text: str) -> str:
    words = {w.strip(".,;:!?¿¡\"'()").lower() for w in text.split()}
    best, score = "other", 0
    for lang, stop in STOP.items():
        hit = len(words & stop)
        if hit > score:
            best, score = lang, hit
    return best if score >= 2 else "other"


def load_items() -> list[dict[str, object]]:
    rng = np.random.default_rng(SEED)
    items: list[dict[str, object]] = []

    def add(dataset: str, idx: int, text: str, label: int, lang: str = "", split: str = "") -> None:
        if isinstance(text, str) and text.strip():
            items.append({"dataset": dataset, "idx": idx, "text": text, "label": label,
                          "lang": lang, "split": split})

    for name, label in (("malicious.jsonl", 1), ("benign.jsonl", 0)):
        for i, line in enumerate((REPO / "tests/detection_corpus" / name).read_text().splitlines()):
            if line.strip():
                row = json.loads(line)
                add(f"inrepo:{row['family']}", i, row["text"], label, "en", row["split"])
    base = DS / "deepset__prompt-injections/data"
    for split, fname in (("train", "train-00000-of-00001-9564e8b05b4757ab.parquet"),
                         ("test", "test-00000-of-00001-701d16158af87368.parquet")):
        for i, row in enumerate(pq.read_table(base / fname).to_pylist()):
            add("deepset", i, row["text"], int(row["label"]), guess_lang(row["text"]), split)
    jack_jb: list[tuple[int, str]] = []
    path = DS / "jackhhao__jailbreak-classification/default/jailbreak_dataset_full.csv"
    with path.open(newline="") as handle:
        for i, row in enumerate(csv.DictReader(handle)):
            if row["type"] == "jailbreak":
                jack_jb.append((i, row["prompt"]))
            else:
                add("jackhhao", i, row["prompt"], 0, "", "full")
    for k in sorted(rng.choice(len(jack_jb), size=SAMPLE["jackhhao:jailbreak"], replace=False)):
        add("jackhhao", jack_jb[int(k)][0], jack_jb[int(k)][1], 1, "", "full")
    base = DS / "Lakera__gandalf_ignore_instructions/data"
    for split, fname in (("train", "train-00000-of-00001-ded53be747ff55cd.parquet"),
                         ("validation", "validation-00000-of-00001-94481a2a09ff2fff.parquet"),
                         ("test", "test-00000-of-00001-bc92128b9288a6d1.parquet")):
        for i, row in enumerate(pq.read_table(base / fname).to_pylist()):
            add("gandalf", i, row["text"], 1, "", split)
    base = DS / "TrustAIRLab__in-the-wild-jailbreak-prompts"
    jb = pq.read_table(base / "jailbreak_2023_12_25/train-00000-of-00001.parquet",
                       columns=["prompt"]).column("prompt").to_pylist()
    for i in sorted(rng.choice(len(jb), size=SAMPLE["inthewild:jailbreak"], replace=False)):
        add("inthewild:jailbreak", int(i), jb[int(i)], 1)
    reg = pq.read_table(base / "regular_2023_12_25/train-00000-of-00001.parquet",
                        columns=["prompt"]).column("prompt").to_pylist()
    for i in sorted(rng.choice(len(reg), size=SAMPLE["inthewild:regular"], replace=False).tolist()):
        add("inthewild:regular", i, reg[i], 0)
    dolly = [json.loads(x) for x in (DS / "databricks__databricks-dolly-15k/databricks-dolly-15k"
                                         ".jsonl").read_text().splitlines()]
    for i in sorted(rng.choice(len(dolly), size=SAMPLE["dolly:instruction"], replace=False).tolist()):
        add("dolly:instruction", i, dolly[i]["instruction"], 0, "en")
    longs = [i for i, row in enumerate(dolly) if len(row["context"]) > 2000]
    for k in sorted(rng.choice(len(longs), size=SAMPLE["dolly:long_context"], replace=False)):
        row = dolly[longs[int(k)]]
        add("dolly:long_context", longs[int(k)], row["instruction"] + "\n\n" + row["context"], 0,
            "en")
    oasst = pq.read_table(DS / "OpenAssistant__oasst1/data/validation-00000-of-00001-"
                          "134b8fd0c89408b6.parquet", columns=["text", "role", "lang"]).to_pylist()
    for i, row in enumerate(oasst):
        if row["role"] == "prompter":
            add("oasst1:prompter", i, row["text"], 0, row["lang"])
    with (DS / "yanismiraoui__prompt_injections/prompt_injections.csv").open(newline="") as handle:
        for i, row in enumerate(csv.DictReader(handle)):
            add("yanis:multilingual", i, row["prompt_injections"], 1,
                guess_lang(row["prompt_injections"]))
    emails = json.loads((DS / "microsoft__llmail-inject-challenge/data/emails_for_fp_tests.json")
                        .read_text())
    for i, text in enumerate(emails):
        add("llmail:benign_email", i, text, 0, "en")
    return items


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    scores = np.concatenate([np.asarray(pos), np.asarray(neg)])
    ranks = scores.argsort().argsort().astype(np.float64) + 1
    r_pos = ranks[: len(pos)].sum()
    return round(float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 4)


class Scorer:
    """Scores windows with a cache keyed by ids (duplicates are common across datasets)."""

    def __init__(self, sess) -> None:
        self.sess = sess
        self.cache: dict[tuple[int, ...], float] = {}
        self.windows_scored = 0

    def score_many(self, rows: list[tuple[int, ...]]) -> list[float]:
        todo = [r for r in dict.fromkeys(rows) if r not in self.cache]
        by_len: dict[int, list[tuple[int, ...]]] = defaultdict(list)
        for r in todo:
            by_len[len(r)].append(r)
        for group in by_len.values():
            for i in range(0, len(group), 8):
                chunk = group[i:i + 8]
                for r, p in zip(chunk, p_malicious(run_rows(self.sess, chunk)), strict=True):
                    self.cache[r] = float(p)
                self.windows_scored += len(chunk)
        return [self.cache[r] for r in rows]


def confusion(pos: list[float], neg: list[float], t: float) -> dict[str, object]:
    tp = sum(1 for s in pos if s >= t)
    fp = sum(1 for s in neg if s >= t)
    return {"tp": tp, "fn": len(pos) - tp, "fp": fp, "tn": len(neg) - fp,
            "recall": round(tp / len(pos), 4) if pos else None,
            "recall_ci95": wilson(tp, len(pos)) if pos else None,
            "fpr": round(fp / len(neg), 4) if neg else None,
            "fpr_ci95": wilson(fp, len(neg)) if neg else None}


def planted_test(tok, scorer: Scorer, items) -> dict[str, object]:
    """Plant one injection in a long benign document: at a random position, and centred on the
    first window boundary (token 510). Score with overlap 0, 64, 128; report several thresholds."""
    rng = np.random.default_rng(SEED + 1)
    docs = [it for it in items if it["dataset"] in ("dolly:long_context", "inthewild:regular")
            and it["label"] == 0 and it["windows"] == 2 and it["score"] < 0.05]
    inj = [it for it in items if it["dataset"] in ("gandalf", "deepset") and it["label"] == 1
           and it["tokens"] <= 120 and it["split"] in ("test", "validation")]
    out: dict[str, object] = {"docs_available": len(docs), "injections_available": len(inj),
                              "pairs": SAMPLE["planted_pairs"]}
    pairs = [(docs[int(rng.integers(len(docs)))], inj[int(rng.integers(len(inj)))])
             for _ in range(SAMPLE["planted_pairs"])]
    alone = scorer.score_many([plan_windows(tok, i["text"]).windows[0].ids for _, i in pairs])
    positions = [int(rng.integers(0, 10**9)) for _ in pairs]
    for placement in ("random", "boundary_510"):
        res: dict[str, object] = {}
        for overlap in (0, 64, 128):
            planted_scores = []
            for (doc, injection), pos in zip(pairs, positions, strict=True):
                text = doc["text"]
                enc = tok.encode(text, add_special_tokens=False)
                if placement == "random":
                    tpos = pos % len(enc.ids)
                else:
                    half = len(tok.encode(injection["text"], add_special_tokens=False).ids) // 2
                    tpos = max(0, min(len(enc.ids) - 1, 510 - half))
                cpos = enc.offsets[tpos][0]
                planted = text[:cpos] + " " + injection["text"] + " " + text[cpos:]
                plan = plan_windows(tok, planted, overlap=overlap)
                planted_scores.append(max(scorer.score_many([w.ids for w in plan.windows])))
            at = {}
            for t in (0.5, 0.8, 0.9, 0.95):
                alone_hit = [i for i, a in enumerate(alone) if a >= t]
                at[str(t)] = {
                    "detected": sum(1 for s in planted_scores if s >= t),
                    "detected_standalone": len(alone_hit),
                    "detected_when_detected_standalone": sum(
                        1 for i in alone_hit if planted_scores[i] >= t),
                }
                n = at[str(t)]["detected_standalone"]
                at[str(t)]["conditional_recall"] = (
                    round(at[str(t)]["detected_when_detected_standalone"] / n, 4) if n else None)
            res[f"overlap_{overlap}"] = at
        out[placement] = res
    return out


def main() -> int:
    tok = load_tokenizer()
    sess = make_session(threads=2)  # 2 intra-op threads to shorten the time holding the lock
    scorer = Scorer(sess)
    items = load_items()
    over_length: dict[str, int] = defaultdict(int)
    kept = []
    for it in items:
        plan = plan_windows(tok, it["text"], overlap=64)
        if len(plan.windows) > MAX_WINDOWS:
            over_length[f"{it['dataset']}:label{it['label']}"] += 1
            continue
        kept.append(it)
        scores = scorer.score_many([w.ids for w in plan.windows])
        it["tokens"] = plan.n_tokens
        it["windows"] = len(plan.windows)
        it["score"] = max(scores)
        it["argmax"] = int(np.argmax(scores))
        it["window_scores"] = scores
    items = kept

    # Per-dataset tables.
    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[it["dataset"]].append(it)
    datasets = {}
    for name, rows in sorted(groups.items()):
        pos = [r["score"] for r in rows if r["label"] == 1]
        neg = [r["score"] for r in rows if r["label"] == 0]
        datasets[name] = {
            "n": len(rows), "positives": len(pos), "negatives": len(neg),
            "windows_total": int(sum(r["windows"] for r in rows)),
            "multi_window_rows": int(sum(1 for r in rows if r["windows"] > 1)),
            "auc": auc(pos, neg),
            "at": {str(t): confusion(pos, neg, t) for t in GRID},
        }
    # Splits that matter: deepset test only; in-repo eval split only.
    for name, pred in (("deepset[test]", lambda r: r["dataset"] == "deepset" and r["split"] == "test"),
                       ("inrepo[eval]", lambda r: r["dataset"].startswith("inrepo:")
                        and r["split"] == "eval")):
        rows = [r for r in items if pred(r)]
        pos = [r["score"] for r in rows if r["label"] == 1]
        neg = [r["score"] for r in rows if r["label"] == 0]
        datasets[name] = {"n": len(rows), "positives": len(pos), "negatives": len(neg),
                          "auc": auc(pos, neg),
                          "at": {str(t): confusion(pos, neg, t) for t in GRID}}

    # Pools used to choose the threshold.
    short_benign = [r["score"] for r in items if r["label"] == 0 and r["windows"] == 1]
    all_benign = [r["score"] for r in items if r["label"] == 0]
    explicit_injection = [r["score"] for r in items if r["label"] == 1 and r["dataset"] in (
        "gandalf", "deepset", "inrepo:prompt_injection", "inrepo:goal_hijacking")]
    jailbreaks = [r["score"] for r in items if r["label"] == 1 and r["dataset"] in (
        "jackhhao", "inthewild:jailbreak", "inrepo:jailbreak")]
    pools = {
        "benign_single_window": len(short_benign), "benign_all": len(all_benign),
        "explicit_injection": len(explicit_injection), "jailbreak": len(jailbreaks),
        "at": {str(t): {"fpr_benign_single_window": confusion([], short_benign, t)["fpr"],
                        "fpr_benign_single_window_ci95": wilson(
                            sum(1 for s in short_benign if s >= t), len(short_benign)),
                        "fpr_benign_all": confusion([], all_benign, t)["fpr"],
                        "fpr_benign_all_ci95": wilson(
                            sum(1 for s in all_benign if s >= t), len(all_benign)),
                        "recall_explicit_injection": confusion(explicit_injection, [], t)["recall"],
                        "recall_jailbreak": confusion(jailbreaks, [], t)["recall"]}
               for t in GRID},
    }

    # FPR by window count (benign only), per-window FPR on benign windows.
    buckets = {"1": (1, 1), "2-3": (2, 3), "4-7": (4, 7), "8+": (8, 10**9)}
    by_w = {}
    for label, (lo, hi) in buckets.items():
        rows = [r for r in items if r["label"] == 0 and lo <= r["windows"] <= hi]
        by_w[label] = {"rows": len(rows), **{
            str(t): round(sum(1 for r in rows if r["score"] >= t) / len(rows), 4) if rows else None
            for t in (0.5, 0.9, 0.95, 0.99)}}
    benign_windows = [s for r in items if r["label"] == 0 for s in r["window_scores"]]
    quantiles = {str(q): round(float(np.quantile(benign_windows, q)), 6)
                 for q in (0.9, 0.95, 0.98, 0.99, 0.995, 0.999)}
    per_window = {str(t): {"windows": len(benign_windows),
                           "fpr": round(sum(1 for s in benign_windows if s >= t)
                                        / len(benign_windows), 5)}
                  for t in (0.5, 0.9, 0.95, 0.99)}

    # Per language: benign FPR (oasst1 prompter, labelled by the dataset) and recall (yanis, guessed).
    lang = {}
    for name, label, key in (("oasst1:prompter", 0, "fpr"), ("yanis:multilingual", 1, "recall"),
                             ("deepset", 1, "recall"), ("deepset", 0, "fpr")):
        per: dict[str, list[float]] = defaultdict(list)
        for r in items:
            if r["dataset"] == name and r["label"] == label:
                per[r["lang"] or "?"].append(r["score"])
        for lg, scores in sorted(per.items()):
            if len(scores) < 10:
                continue
            hits = [sum(1 for s in scores if s >= t) for t in (0.5, 0.9)]
            lang[f"{name}:{lg}:{key}"] = {"n": len(scores),
                                          "at_0.5": round(hits[0] / len(scores), 4),
                                          "at_0.9": round(hits[1] / len(scores), 4)}

    chosen = float(sys.argv[1]) if len(sys.argv) > 1 else 0.9
    planted = planted_test(tok, scorer, items)

    summary = {
        "windowing": {"payload": 510, "overlap": 64, "aggregate": "max"},
        "items": len(items), "windows_scored_unique": scorer.windows_scored,
        "sample_sizes": SAMPLE, "max_windows": MAX_WINDOWS,
        "over_length_excluded": dict(over_length),
        "datasets": datasets, "pools": pools, "fpr_by_window_count": by_w,
        "per_window_benign_fpr": per_window, "benign_window_score_quantiles": quantiles, "per_language": lang, "planted": planted,
        "chosen_threshold": chosen,
    }
    (EVIDENCE_DIR / "results" / "eval_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (EVIDENCE_DIR / "results" / "eval_rows.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dataset", "split", "idx", "label", "lang", "tokens", "windows", "score",
                         "argmax_window"])
        for r in items:
            writer.writerow([r["dataset"], r["split"], r["idx"], r["label"], r["lang"],
                             r["tokens"], r["windows"], f"{r['score']:.6f}", r["argmax"]])
    print(json.dumps({"items": len(items), "windows": scorer.windows_scored,
                      "pools": pools["at"]["0.9"], "planted": planted}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
