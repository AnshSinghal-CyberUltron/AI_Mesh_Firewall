#!/usr/bin/env python3
"""Generate shared/words.json: the synthprov output-token list and the olg nonce alphabet.

Why this exists: the Llama-Prompt-Guard-2 tokenizers DELETE every space before Unigram
segmentation (normalizer: Replace " " -> ""), so adjacent words can merge across word
boundaries ("rvn able" -> "▁rv" + "nable").  A per-request nonce built from arbitrary words
would therefore change the input token count from request to request.  This script keeps
only nonce words that (a) are a single non-initial piece in BOTH PG2 vocabularies and
(b) never merge with any other kept word or with the nonce delimiters, then verifies the
property empirically on random nonces in context.  Result: every nonce costs exactly the
same number of PG2 tokens, so make_corpus.py token counts hold for every request.

Usage: python gen_words.py --models <SP>/models --out shared/words.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from tokenizers import Tokenizer

# Hand-authored benign candidates for the nonce alphabet (common English nouns/adjectives).
NONCE_CANDIDATES = """
apple river stone cloud garden window table paper pencil orange silver golden forest meadow
valley island harbor bridge castle market school doctor farmer singer player writer reader
teacher driver pilot sailor baker hunter painter dancer camera guitar piano violin flute drum
rocket planet comet galaxy ocean desert canyon glacier volcano jungle prairie tundra lagoon
beach coast shore wave tide breeze storm thunder rain snow frost spring summer autumn winter
morning evening night today tomorrow monday friday sunday january march april june july august
october number letter story poem novel chapter page book library museum theater cinema concert
festival holiday journey travel ticket station airport train plane boat ship truck bicycle
wagon engine motor wheel circle square triangle point angle shape color purple yellow green
brown black white pink gray bright quiet gentle simple happy lucky brave clever honest humble
kind calm warm cool fresh sweet bitter salty spicy crisp smooth rough heavy light quick slow
early late first second third final north south east west center middle corner edge border
field farm barn fence gate door wall roof floor stair kitchen bedroom garage office desk chair
sofa lamp clock mirror carpet pillow blanket basket bottle cup plate bowl spoon fork bread
butter cheese honey sugar flour salt pepper rice bean corn wheat carrot potato tomato onion
garlic lemon cherry grape peach pear plum melon banana mango coconut walnut almond peanut
cookie cake pie soup salad pasta pizza noodle coffee tea milk juice water animal horse rabbit
turtle dolphin whale eagle falcon owl parrot sparrow robin swan duck goose chicken lion tiger
bear wolf fox deer moose zebra giraffe monkey panda koala kitten puppy pony lamb calf bee
butterfly spider ant flower rose tulip daisy lily orchid maple oak pine cedar willow birch
bamboo cactus moss fern grass leaf branch root seed harvest bell candle ribbon button marble
pebble crystal diamond pearl ruby emerald copper iron bronze nickel velvet cotton linen wool
silk leather canvas denim anchor arrow ladder hammer shovel bucket rope chain magnet compass
lantern mountain hill cliff cave pond lake stream creek bay cape reef dune marsh swamp orchard
vineyard cottage cabin tower temple palace garden village city town county nation country
planet moon star sun sky earth cloud shadow mirror echo signal rhythm melody harmony chorus
dance song music picture photo sketch drawing statue model puzzle game toy kite balloon
blossom petal sprout acorn pumpkin olive raisin muffin bagel waffle pancake yogurt cereal
""".split()

# Provider output tokens (1 token per SSE chunk): common short benign words with a leading
# space, plus sentence punctuation.  Weighted by repetition in the list.
OUTPUT_WORDS = """
the and for you that with this from have are was not but all can one will more when some time
them then than into only over also back after use two how our work first well way even new want
any these give day most is it in on of to as at be by do go if my no or so up we an
make like just know take people year good some could see other look come think about many
because there their would which what very much here where why each small large number place
water light world house study point plan note list step part rest idea case fact line
""".split()
PUNCT = [".", ",", ".", ","]  # each twice as frequent as a single word


def load(models: Path, name: str) -> tuple[Tokenizer, str]:
    p = models / f"Llama-Prompt-Guard-2-{name}" / "tokenizer.json"
    t = Tokenizer.from_file(str(p))
    t.no_padding()
    t.no_truncation()
    return t, hashlib.sha256(p.read_bytes()).hexdigest()


def counts(tok: Tokenizer, texts: list[str]) -> list[int]:
    return [len(e.ids) for e in tok.encode_batch(texts, add_special_tokens=False)]


def nonce_text(words: list[str]) -> str:
    # MUST match rv.NonceText in internal/rv/nonce.go.  The '-' separators stop the
    # space-deleting PG2 normalizer from merging adjacent words (verified below).
    return "(ref-" + "-".join(words) + ")"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--nonce-size", type=int, default=256)
    ap.add_argument("--verify", type=int, default=200_000)
    args = ap.parse_args()
    models = Path(args.models)
    toks = {n: load(models, n) for n in ("22M", "86M")}

    cands = sorted(set(w for w in NONCE_CANDIDATES if w.isalpha() and w.islower()))
    # (a) a word plus its separator adds exactly two tokens in both tokenizers
    ref = {n: counts(t, ["(ref-x)"])[0] for n, (t, _) in toks.items()}
    ok = [w for w in cands
          if all(counts(t, [f"(ref-{w}-x)"])[0] == ref[n] + 2 for n, (t, _) in toks.items())]
    print(f"candidates={len(cands)} separator-clean={len(ok)}", file=sys.stderr)
    # (b) empirical verification with pruning: random 6-word nonces alone and embedded in
    # text must all tokenize to one count; words seen in a failing sample are removed.
    rng = random.Random(7)
    ctx_before = ["Please help me.", "Summary:", "hello", "The report says", "", "Note 42"]
    ctx_after = ["Please explain the result.", "What is next?", "thanks", "Tell me more", "", "ok."]
    alive = set(ok)
    for _round in range(10):
        words = sorted(alive)
        bad: set[str] = set()
        verify = {}
        for n, (t, _) in toks.items():
            samples = [[rng.choice(words) for _ in range(6)] for _ in range(args.verify)]
            ctx = [(rng.choice(ctx_before), rng.choice(ctx_after)) for _ in samples]
            ca = counts(t, [nonce_text(ws) for ws in samples])
            cb = counts(t, [b + " " + nonce_text(ws) + " " + a for ws, (b, a) in zip(samples, ctx)])
            cr = counts(t, [b + " " + nonce_text(["x"] * 6) + " " + a for b, a in ctx])
            mode = max(set(ca), key=ca.count)
            # standalone must equal the mode; embedded delta vs the reference nonce must be constant
            ref6 = counts(t, [nonce_text(["x"] * 6)])[0]
            deltas = [y - z for y, z in zip(cb, cr)]
            dmode = max(set(deltas), key=deltas.count)
            for ws, x, d in zip(samples, ca, deltas):
                if x != mode or d != dmode:
                    bad.update(ws)
            verify[n] = {"alone_token_counts": sorted(set(ca)), "embedded_delta_vs_ref": sorted(set(deltas)),
                         "ref_nonce_tokens": ref6, "samples": args.verify}
        print(f"round {_round}: words={len(words)} bad={len(bad)} {verify}", file=sys.stderr)
        if not bad:
            break
        alive -= bad
    clean = sorted(alive)
    if len(clean) < args.nonce_size:
        print(f"ERROR: only {len(clean)} clean words, need {args.nonce_size}", file=sys.stderr)
        return 2
    nonce_words = sorted(random.Random(20260923).sample(clean, args.nonce_size))
    # final verification on exactly the published alphabet
    final = {}
    for n, (t, _) in toks.items():
        rng2 = random.Random(99)
        samples = [[rng2.choice(nonce_words) for _ in range(6)] for _ in range(args.verify)]
        final[n] = sorted(set(counts(t, [nonce_text(ws) for ws in samples])))
    verify = {"final_alone_token_counts": final, "pruning_rounds": _round + 1}
    base = {n: final[n][0] for n in final}

    # output-token stats: provider tokens -> PG2 tokens ratio on a long deterministic sample
    out_tokens = [" " + w for w in OUTPUT_WORDS] + PUNCT
    rng = random.Random(11)
    sample = [rng.choice(out_tokens) for _ in range(20000)]
    text = "".join(sample)
    ratio = {n: counts(t, [text])[0] / len(sample) for n, (t, _) in toks.items()}
    avg_chars = sum(len(x) for x in sample) / len(sample)

    doc = {
        "generated_by": "gen_words.py",
        "tokenizers": {n: {"sha256": h} for n, (_, h) in toks.items()},
        "nonce_format": "(ref-w1-w2-w3-w4-w5-w6)  words: run-tag, loadgen-index, 4 x base-256 digits of seq",
        "nonce_tokens": base,
        "nonce_verification": verify,
        "nonce_words": nonce_words,
        "out_tokens": out_tokens,
        "out_tokens_avg_chars": round(avg_chars, 3),
        "out_pg2_tokens_per_provider_token": {n: round(r, 4) for n, r in ratio.items()},
    }
    Path(args.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k not in ("nonce_words", "out_tokens")}, indent=1))
    ok_all = all(len(v) == 1 for v in final.values())
    print("NONCE_TOKEN_COUNT_STABLE" if ok_all else "NONCE_TOKEN_COUNT_UNSTABLE", file=sys.stderr)
    return 0 if ok_all else 3


if __name__ == "__main__":
    sys.exit(main())
