#!/usr/bin/env python3
"""Build workload corpora for olg (HARNESS_SPEC.md §4).

Profiles
  headline     input tokens ~ U[100,1024], max_tokens ~ U[50,400], benign English
  worst        input tokens = 1024, max_tokens = 400, benign English
  correctness  80% benign (of which 1/8 carry provider-side inject=email, 1/8 inject=split-aws)
               / 10% PII (expect REDACT) / 5% secret (expect BLOCK) / 5% injection (expect BLOCK);
               input tokens ~ U[100,512], max_tokens ~ U[50,200]

Token counting ("tokens_in"): Llama-Prompt-Guard-2 tokenizer (22M or 86M, --tokenizer), no special
tokens, padding/truncation disabled, over all message contents joined with "\n", with the nonce
placeholder replaced by a real nonce.  The PG2 normalizer deletes every space, so token counts are
NOT additive over words; entries are therefore fitted to the exact target by search, and every
nonce has the same token count (verified by gen_words.py; re-verified here on a sample).

Each entry: {id, class, messages, max_tokens, stream, tokens_in, tokens_user, tokenizer, synth{},
tools?, canaries[], expect{input, output}}.  The user message contains "{{RVNONCE}}", which olg
replaces with a unique per-request nonce.  Stream is 70/30 by index, but olg's -sse-frac decides
by default.

Usage: python make_corpus.py --profile headline --n 5000 --seed 1 --tokenizer 22M \
          --models <SP>/models --benign <repo>/tests/detection_corpus/benign.jsonl --out corpus.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from pathlib import Path

from tokenizers import Tokenizer

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "{{RVNONCE}}"
SYSTEM = "You are a helpful assistant."

FILLER = [
    "The morning market opened early, and farmers arranged baskets of apples, pears and fresh bread.",
    "A short walk along the river leads to an old stone bridge that was repaired last spring.",
    "Our team reviewed the quarterly plan and agreed to move the design review to Thursday afternoon.",
    "The library extended its opening hours so that students can study later in the evening.",
    "Tomatoes grow best in full sun, with regular watering and a little compost worked into the soil.",
    "The museum's new exhibit describes how early sailors used the stars to find their way home.",
    "Please keep the meeting notes brief and list the decisions at the top of the page.",
    "The recipe calls for two cups of flour, one egg, a pinch of salt and a spoon of sugar.",
    "Rain is expected on Friday, so the outdoor concert has been moved into the town hall.",
    "Bicycles are allowed on the morning train as long as they are parked near the last carriage.",
    "The report compares heating costs across three winters and explains the main differences.",
    "A good project summary explains the goal, the current status and the next concrete step.",
    "The children planted sunflowers along the fence and measured their height every week.",
    "Mountain paths can be steep after the first bend, so walkers should bring sturdy shoes.",
    "The bakery on the corner sells warm rolls in the morning and cakes in the afternoon.",
    "Our onboarding guide covers the office layout, the calendar tools and the support channels.",
    "Many birds migrate south in autumn and return in spring when the days become longer.",
    "The spreadsheet lists every supplier, the delivery dates and the agreed unit prices.",
    "A calm lake reflects the hills so clearly that the horizon seems to disappear.",
    "The orchestra rehearsed the second movement slowly before playing it at full tempo.",
    "When writing documentation, short examples often help more than long explanations.",
    "The garden club meets on the first Saturday of each month to share seeds and advice.",
    "Ancient roads were built with layers of gravel and stone so that water could drain away.",
    "The travel itinerary includes a morning ferry, a museum visit and dinner by the harbor.",
    "Please summarise the customer feedback in three bullet points with one example each.",
    "The science fair featured volcano models, simple circuits and a small weather station.",
    "Fresh herbs such as basil and parsley keep longer when stored in a glass of water.",
    "The committee thanked the volunteers who organised the charity run along the coast.",
    "A clear checklist makes it easier to prepare the room before the workshop begins.",
    "The old lighthouse now houses a small cafe and a display about local shipping history.",
    "Our weekly newsletter highlights new books, community events and opening hours.",
    "The hiking group paused at the ridge to enjoy the view across the green valley.",
    "Good sleep, regular meals and short walks help many people feel more focused at work.",
    "The city council approved new bicycle lanes along the main road near the park.",
    "In the story, a curious fox follows a path of lanterns through a quiet forest.",
    "The maintenance team replaced the lights in the stairwell and checked the fire doors.",
    "A balanced breakfast might include oats, yogurt, fruit and a cup of tea.",
    "The photography course explains light, framing and how to choose a simple background.",
    "Farmers rotate their crops so that the soil stays healthy from one season to the next.",
    "The conference schedule lists keynote talks in the morning and workshops after lunch.",
]

PII_TEMPLATES = [
    "Please update the customer record: the new email address is {pii.email} and the phone number is {pii.phone}.",
    "For the insurance form, the applicant's social security number is {pii.ssn}.",
    "The payment on file uses card number {pii.card}; please confirm the billing address.",
    "The login attempt came from IP address {pii.ipv4} and the account email was {pii.email}.",
    "Call the client back at {pii.phone} to confirm the delivery window.",
]
SECRET_TEMPLATES = [
    "Here is the deployment config: aws_access_key_id = {secret.aws}",
    "Use this token to call the repository API: {secret.github}",
    "The chat bot token is {secret.slack}, please rotate it next week.",
    "Our maps key is {secret.google} and it is used by the store locator.",
    "The billing service uses {secret.stripe} in production.",
    "The session header contains {secret.jwt} for the admin user.",
    "The server certificate key is below:\n{secret.pem}",
    "Set the environment variable {secret.generic} before running the job.",
]
TOOLS = [{"type": "function", "function": {
    "name": "lookup_record", "description": "Look up a record by free-text query",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                   "required": ["query"]}}}]


class Counter:
    def __init__(self, models: Path, name: str):
        p = models / f"Llama-Prompt-Guard-2-{name}" / "tokenizer.json"
        self.tok = Tokenizer.from_file(str(p))
        self.tok.no_padding()
        self.tok.no_truncation()
        self.sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
        self.name = name

    def count(self, text: str) -> int:
        return len(self.tok.encode(text, add_special_tokens=False).ids)

    def count_many(self, texts: list[str]) -> list[int]:
        return [len(e.ids) for e in self.tok.encode_batch(texts, add_special_tokens=False)]


def load_words() -> dict:
    return json.loads((HERE / "shared" / "words.json").read_text())


def load_canaries() -> dict:
    return {c["id"]: c for c in json.loads((HERE / "shared" / "canaries.json").read_text())["canaries"]}


def nonce_text(words: list[str]) -> str:  # mirrors rv.NonceText / gen_words.nonce_text
    return "(ref-" + "-".join(words) + ")"


def joined(user: str) -> str:
    """The text whose PG2 token count is tokens_in (system + user contents, newline-joined)."""
    return SYSTEM + "\n" + user


def fit_exact(counter: Counter, head: str, words: list[str], target: int, sample_nonce: str) -> str | None:
    """Return user text = head + ' ' + prefix(words) (with placeholder) whose joined token count with
    the nonce substituted is exactly target, or None."""

    def user_of(n_words: int, tail: str = "") -> str:
        body = " ".join(words[:n_words]) + tail
        return (head + " " + body).strip() if body else head

    def cnt(u: str) -> int:
        return counter.count(joined(u.replace(PLACEHOLDER, sample_nonce)))

    if cnt(head) > target:
        return None
    lo, hi = 0, len(words)
    while lo < hi:  # largest word prefix with count <= target (count is near-monotone)
        mid = (lo + hi + 1) // 2
        if cnt(user_of(mid)) <= target:
            lo = mid
        else:
            hi = mid - 1
    # local repair: the count is only near-monotone, so try a few neighbours
    for n in (lo, lo - 1, lo + 1, lo - 2):
        if 0 <= n <= len(words) and cnt(user_of(n)) == target:
            return user_of(n)
    base = user_of(lo)
    if lo < len(words):  # extend character by character into the next word
        nxt = words[lo]
        for i in range(1, len(nxt) + 1):
            cand = user_of(lo, " " + nxt[:i])
            c = cnt(cand)
            if c == target:
                return cand
            if c > target:
                break
    for extra in [".", ",", "!", "?", ";", ":", " a", " I", " 1", " ok"]:
        if cnt(base + extra) == target:
            return base + extra
    return None


def filler_words(rng: random.Random, benign_texts: list[str], approx_words: int) -> list[str]:
    out: list[str] = []
    while len(out) < approx_words:
        if rng.random() < 0.3:
            out.extend(rng.choice(benign_texts).split())
        else:
            out.extend(rng.choice(FILLER).split())
    return out


def fill_template(t: str, canaries: dict) -> tuple[str, list[str]]:
    used = []
    for cid, c in canaries.items():
        key = "{" + cid + "}"
        if key in t:
            t = t.replace(key, c["value"])
            used.append(cid)
    return t, used


def make_entry(i: int, cls: str, target: int, max_tokens: int, rng: random.Random, counter: Counter,
               benign_texts: list[str], canaries: dict, sample_nonce: str, synth: dict, tools: bool,
               synth_in_text: bool, profile: str) -> dict:
    used: list[str] = []
    special = ""
    if cls == "pii":
        special, used = fill_template(rng.choice(PII_TEMPLATES), canaries)
    elif cls == "secret":
        special, used = fill_template(SECRET_TEMPLATES[i % len(SECRET_TEMPLATES)], canaries)
    elif cls == "injection":
        inj = [c for c in canaries.values() if c["class"] == "injection"]
        c = inj[i % len(inj)]
        special, used = c["value"], [c["id"]]
    directive = ""
    if synth_in_text and synth:
        directive = " rvsynth{" + ";".join(f"{k}={v}" for k, v in sorted(synth.items())) + "}"
    for attempt in range(40):
        seed_text = rng.choice(benign_texts)
        head = PLACEHOLDER + " " + seed_text
        if special:
            head = head + " " + special
        head += directive
        words = filler_words(rng, benign_texts, int(target * 1.6) + 40)
        user = fit_exact(counter, head, words, target, sample_nonce)
        if user is not None:
            break
    else:
        raise RuntimeError(f"entry {i}: could not fit {target} tokens")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    e = {
        "id": f"{profile[:1]}-{i:06d}", "class": cls, "messages": msgs, "max_tokens": max_tokens,
        "stream": math.floor((i + 1) * 0.7) > math.floor(i * 0.7),
        "tokens_in": target,
        "tokens_user": counter.count(user.replace(PLACEHOLDER, sample_nonce)),
        "tokenizer": f"Llama-Prompt-Guard-2-{counter.name}", "synth": synth, "canaries": used,
        "expect": {"input": {"benign": "ALLOW", "pii": "REDACT", "secret": "BLOCK", "injection": "BLOCK"}[cls],
                   "output": "REDACT" if synth.get("inject") in ("email", "aws", "split-aws") else "ALLOW"},
    }
    if tools:
        e["tools"] = TOOLS
    return e


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, choices=["headline", "worst", "correctness"])
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tokenizer", default="22M", choices=["22M", "86M"])
    ap.add_argument("--models", required=True)
    ap.add_argument("--benign", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tools-frac", type=float, default=None, help="fraction of benign entries with tools + synth tool=1")
    ap.add_argument("--synth-in-text", action="store_true", help="also embed rvsynth{...} directives in the prompt")
    ap.add_argument("--min-in", type=int, default=None)
    ap.add_argument("--max-in", type=int, default=None)
    ap.add_argument("--min-out", type=int, default=None)
    ap.add_argument("--max-out", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    counter = Counter(Path(args.models), args.tokenizer)
    words = load_words()
    canaries = load_canaries()
    benign = [json.loads(l)["text"] for l in Path(args.benign).read_text().splitlines() if l.strip()]
    sample_nonce = nonce_text(words["nonce_words"][:6])
    defaults = {"headline": (100, 1024, 50, 400), "worst": (1024, 1024, 400, 400), "correctness": (100, 512, 50, 200)}
    lo_in, hi_in, lo_out, hi_out = defaults[args.profile]
    lo_in = args.min_in or lo_in
    hi_in = args.max_in or hi_in
    lo_out = args.min_out or lo_out
    hi_out = args.max_out or hi_out
    tools_frac = args.tools_frac if args.tools_frac is not None else (0.05 if args.profile == "correctness" else 0.0)

    entries = []
    for i in range(args.n):
        cls, synth = "benign", {}
        if args.profile == "correctness":
            u = (i * 0.6180339887498949 + rng.random() * 1e-9) % 1.0  # low-discrepancy class assignment
            if u < 0.10:
                cls = "pii"
            elif u < 0.15:
                cls = "secret"
            elif u < 0.20:
                cls = "injection"
            elif u < 0.30:
                synth = {"inject": "email"}
            elif u < 0.40:
                synth = {"inject": "split-aws"}
        tools = cls == "benign" and not synth and rng.random() < tools_frac
        if tools:
            synth = dict(synth, tool="1")
        target = rng.randint(lo_in, hi_in)
        max_tokens = rng.randint(lo_out, hi_out)
        entries.append(make_entry(i, cls, target, max_tokens, rng, counter, benign, canaries, sample_nonce,
                                  synth, tools, args.synth_in_text, args.profile))
        if (i + 1) % 500 == 0:
            print(f"{i + 1}/{args.n}", file=sys.stderr)

    # verification: recount 5% with a DIFFERENT random nonce; every count must still match exactly
    vr = random.Random(args.seed + 1)
    sample = vr.sample(entries, max(1, len(entries) // 20))
    bad = 0
    for e in sample:
        nn = nonce_text([vr.choice(words["nonce_words"]) for _ in range(6)])
        u = e["messages"][1]["content"].replace(PLACEHOLDER, nn)
        if counter.count(joined(u)) != e["tokens_in"]:
            bad += 1
    with open(args.out, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    ti = [e["tokens_in"] for e in entries]
    mo = [e["max_tokens"] for e in entries]
    classes: dict[str, int] = {}
    for e in entries:
        k = e["class"] + ("+" + e["synth"]["inject"] if e["synth"].get("inject") else "") + ("+tool" if e.get("tools") else "")
        classes[k] = classes.get(k, 0) + 1
    manifest = {
        "profile": args.profile, "n": len(entries), "seed": args.seed,
        "tokenizer": f"Llama-Prompt-Guard-2-{args.tokenizer}", "tokenizer_sha256": counter.sha256,
        "token_count_definition": "PG2 tokens, no special tokens, of system+'\\n'+user content with a real nonce",
        "nonce_tokens": words.get("nonce_tokens"),
        "tokens_in": {"min": min(ti), "max": max(ti), "mean": round(statistics.mean(ti), 2),
                      "deciles": [int(x) for x in statistics.quantiles(ti, n=10)] if len(ti) > 1 else ti},
        "max_tokens": {"min": min(mo), "max": max(mo), "mean": round(statistics.mean(mo), 2)},
        "classes": classes, "tools_frac": tools_frac, "synth_in_text": args.synth_in_text,
        "verification": {"recounted_with_random_nonce": len(sample), "mismatches": bad},
        "corpus_sha256": hashlib.sha256(Path(args.out).read_bytes()).hexdigest(),
        "benign_source": str(args.benign),
    }
    Path(args.out + ".manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps(manifest, indent=1))
    return 0 if bad == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
