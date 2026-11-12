import random, re, sys, warnings
sys.path.insert(0, ".")
from gateway_v2.detect.conditions import group_depth
from re import _parser as P, _constants as C
warnings.simplefilter("ignore")

def true_depth(sub):
    best, stack = 0, [(sub, 0)]
    while stack:
        node, d = stack.pop()
        best = max(best, d)
        for op, av in (node.data if isinstance(node, P.SubPattern) else node):
            if op is C.SUBPATTERN: stack.append((av[3], d + 1))
            elif op in (C.ASSERT, C.ASSERT_NOT): stack.append((av[1], d + 1))
            elif op is C.ATOMIC_GROUP: stack.append((av, d + 1))
            elif op is C.GROUPREF_EXISTS:
                stack.append((av[1], d + 1))
                if av[2] is not None: stack.append((av[2], d + 1))
            elif op is C.BRANCH: stack.extend((b, d) for b in av[1])
            elif op in (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT): stack.append((av[2], d))
    return best

JUNK = "()[]^\\#\n a|-x?:"
def junk(rng, n, forbid=""):
    out = []
    for _ in range(n):
        c = rng.choice(JUNK)
        if c == "\\": c = "\\" + rng.choice("()[]#an\\")
        if c in forbid: continue
        out.append(c)
    return "".join(out)

OPEN = ["(", "(?:", "(?=", "(?!", "(?<!", "(?>", "(?i:", "(?x:", "(?-x:"]
def gen(rng):
    parts = ["(?x)"] if rng.random() < 0.3 else []
    depth = 0
    for _ in range(rng.randint(1, 30)):
        r = rng.random()
        if r < 0.25:
            parts.append(rng.choice(OPEN)); depth += 1
        elif r < 0.45 and depth:
            parts.append(")" + rng.choice(["", "*", "+", "?", "{2}"])); depth -= 1
        elif r < 0.6:
            body = junk(rng, rng.randint(0, 6), forbid="]")
            parts.append("[" + rng.choice(["", "^"]) + rng.choice(["", "]"]) + body.replace("\\", "") + "a]")
        elif r < 0.7:
            parts.append("(?#" + junk(rng, rng.randint(0, 6), forbid=")") + ")")
        elif r < 0.8:
            parts.append("#" + junk(rng, rng.randint(0, 6), forbid="\n") + "\n")
        elif r < 0.9:
            parts.append(rng.choice(["\\(", "\\)", "\\[", "\\]", "\\\\", "\\#"]))
        else:
            parts.append(rng.choice(["a", "b|c", " "]))
    parts.append(")" * depth)
    return "".join(parts)

rng = random.Random(int(sys.argv[1]))
checked = under = 0
for _ in range(int(sys.argv[2])):
    p = gen(rng)
    try: tree = P.parse(p)
    except (re.error, RecursionError, OverflowError): continue
    checked += 1
    t, s = true_depth(tree), group_depth(p)
    if s < t:
        under += 1
        if under < 5: print(repr(p), t, s)
print(f"checked={checked} under_counts={under}")
