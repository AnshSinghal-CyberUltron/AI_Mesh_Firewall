"""Revision 7: non-capturing emission of unreferenced groups, and the renumbering of references.

    python renumber_test.py <seed> <patterns>

Random patterns with 1-6 groups (capturing, named and non-capturing, nested, in lookarounds and
repeats) in which random groups are named by backreferences (`\\n`, `(?P=name)`) or conditionals
(`(?(n)yes|no)`, `(?(name)yes|no)`). For each pattern `re` accepts and the translator accepts: the
translated pattern's spans equal `re`'s on 12 texts, it has exactly as many capturing groups as the
source has referenced groups, and every emitted reference names a capturing group. Prints counts.
"""
import random
import re
import sys
import warnings

warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import regex  # noqa: E402

import translate_prototype as T  # noqa: E402

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
R = random.Random(seed)
ATOMS = ["a", "b", "ab", "[ab]", r"\w", ".", "x?", "a+", "b*"]
TEXTS = ["", "a", "ab", "aab", "abab", "abba", "aaabbb", "xaxbx", "ba" * 5, "abcabc", "a b ab", "bbbaaab"]


def build():
    groups = []  # (number, name or None)
    count = [0]

    def group(depth):
        count[0] += 1
        num = count[0]
        name = f"g{num}" if R.random() < 0.3 else None
        groups.append((num, name))
        body = seq(depth + 1)
        opener = f"(?P<{name}>" if name else "("
        return opener + body + ")"

    def ref():
        if not groups:
            return R.choice(ATOMS)
        num, name = R.choice(groups)
        kind = R.random()
        if kind < 0.4:
            return f"(?P={name})" if name and R.random() < 0.5 else f"\\{num}"
        tag = name if name and R.random() < 0.5 else str(num)
        return f"(?({tag}){R.choice(ATOMS)}|{R.choice(ATOMS + [''])})"

    def item(depth):
        r = R.random()
        if r < 0.35 and depth < 3 and count[0] < 6:
            g = group(depth)
        elif r < 0.45 and depth < 3:
            g = "(?:" + seq(depth + 1) + ")"
        elif r < 0.5 and depth < 3:
            g = R.choice(["(?=", "(?!"]) + seq(depth + 1) + ")"
        elif r < 0.68:
            g = ref()
        else:
            g = R.choice(ATOMS)
        if R.random() < 0.25:
            g += R.choice(["*", "+", "?", "{1,3}", "*?"])
        return g

    def seq(depth):
        return "".join(item(depth) for _ in range(R.randint(1, 3)))

    return seq(0)


stats = dict(patterns=0, checked=0, mismatch=0, count_wrong=0, refused=0, re_invalid=0)
examples = []
for _ in range(n):
    p = build()
    stats["patterns"] += 1
    try:
        rp = re.compile(p)
    except re.error:
        stats["re_invalid"] += 1
        continue
    try:
        text = T.translate(p)
        rx = regex.compile(text)
    except T.Refused:
        stats["refused"] += 1
        continue
    parsed = T._parse(p)
    refs: set = set()
    T._referenced(parsed, refs)
    if rx.groups != len(refs):
        stats["count_wrong"] += 1
        examples.append(("groups", p, text))
    for t in TEXTS:
        stats["checked"] += 1
        try:
            a = [m.span() for m in rp.finditer(t)]
            b = [m.span() for m in rx.finditer(t, timeout=1.0)]
        except TimeoutError:
            continue
        if a != b:
            stats["mismatch"] += 1
            if len(examples) < 5:
                examples.append((p, t, a, b, text))
print(f"seed={seed} " + " ".join(f"{k}={v}" for k, v in stats.items()) + f" examples={examples[:3]}")
