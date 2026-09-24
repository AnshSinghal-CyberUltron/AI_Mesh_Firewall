#!/usr/bin/env python3
"""Exp F: characters per token for both PG2 tokenizers, by content type.

chars/token = sum(len(text)) / sum(content tokens) (no [CLS]/[SEP]) over each set, computed per sample and
pooled. Sets:
  corpus/developer_traffic, corpus/general_benign  (tests/detection_corpus/benign.jsonl families)
  corpus/code_heuristic vs corpus/prose_heuristic   (benign samples split by a code-marker regex)
  repo_markdown  : every docs/**/*.md in the repo (real markdown: tables, lists, code spans)
  repo_python    : gateway_v2/**/*.py + scripts/**/*.py (source code)
  prose_license  : Llama-4 license + model-card prose paragraphs shipped with the model (English prose)
Also reports windows (510 content tokens each) needed for 10,000 characters and characters per 1,024 tokens.
"""
import glob
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402

CODE_RX = re.compile(r"`|;|\b(SELECT|UPDATE|INSERT|DELETE|FROM|WHERE)\b|[{}()\[\]=<>/\\_]|\.\w{1,4}\b|--")


def stats(tok, texts):
    chars = sum(len(t) for t in texts)
    toks = sum(len(C.content_ids(tok, t)) for t in texts)
    per = [len(t) / max(1, len(C.content_ids(tok, t))) for t in texts]
    cpt = chars / toks
    return {"n_texts": len(texts), "chars": chars, "tokens": toks, "chars_per_token": round(cpt, 3),
            "mean_of_per_text_ratio": round(sum(per) / len(per), 3),
            "windows_for_10000_chars": math.ceil(10000 / cpt / C.CONTENT),
            "chars_per_1024_tokens": round(1024 * cpt, 1)}


def main():
    repo, models, out = Path(sys.argv[1]), sys.argv[2].split(","), sys.argv[3]
    rows = [json.loads(l) for l in open(repo / "tests/detection_corpus/benign.jsonl")]
    sets = {
        "corpus/developer_traffic": [r["text"] for r in rows if r["family"] == "developer_traffic"],
        "corpus/general_benign": [r["text"] for r in rows if r["family"] == "general_benign"],
        "corpus/code_heuristic": [r["text"] for r in rows if CODE_RX.search(r["text"])],
        "corpus/prose_heuristic": [r["text"] for r in rows if not CODE_RX.search(r["text"])],
        "corpus/all_benign": [r["text"] for r in rows],
    }
    md = sorted(glob.glob(str(repo / "docs/**/*.md"), recursive=True))
    sets["repo_markdown"] = [Path(p).read_text(errors="ignore") for p in md]
    py = sorted(glob.glob(str(repo / "gateway_v2/**/*.py"), recursive=True) + glob.glob(str(repo / "scripts/**/*.py"), recursive=True))
    sets["repo_python"] = [Path(p).read_text(errors="ignore") for p in py if "/.venv/" not in p]
    mdir = Path(models[0]).parent
    lic = (mdir / "Llama-Prompt-Guard-2-22M/LICENSE").read_text(errors="ignore")
    card = (mdir / "Llama-Prompt-Guard-2-22M/README.md").read_text(errors="ignore")
    card_prose = [p for p in re.split(r"\n\s*\n", card.split("---", 2)[-1]) if len(p) > 200 and "|" not in p and "```" not in p]
    sets["prose_license"] = [lic] + card_prose
    res = {"sets_meta": {k: len(v) for k, v in sets.items()}, "code_regex": CODE_RX.pattern, "by_tokenizer": {}}
    for m in models:
        tok = C.load_tokenizer(m)
        res["by_tokenizer"][Path(m).name] = {k: stats(tok, v) for k, v in sets.items() if v}
        for k, v in res["by_tokenizer"][Path(m).name].items():
            print(f"{Path(m).name:28s} {k:28s} n={v['n_texts']:5d} chars/token={v['chars_per_token']:.3f} "
                  f"windows@10k={v['windows_for_10000_chars']} chars@1024tok={v['chars_per_1024_tokens']}")
    Path(out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
