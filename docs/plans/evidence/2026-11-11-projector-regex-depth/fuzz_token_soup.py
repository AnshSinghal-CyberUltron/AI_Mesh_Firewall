import random, re, sys, warnings
sys.path.insert(0, ".")
from gateway_v2.detect.conditions import group_depth
from re import _parser as P, _constants as C
warnings.simplefilter("ignore")

GROUPS = {C.SUBPATTERN, C.ASSERT, C.ASSERT_NOT, C.ATOMIC_GROUP, C.GROUPREF_EXISTS}
def true_depth(sub):
    # iterative walk of re's parse tree; depth counts group-like nodes
    best = 0
    stack = [(sub, 0)]
    while stack:
        node, d = stack.pop()
        best = max(best, d)
        items = node.data if isinstance(node, P.SubPattern) else node
        for op, av in items:
            if op is C.SUBPATTERN:
                stack.append((av[3], d + 1))
            elif op in (C.ASSERT, C.ASSERT_NOT):
                stack.append((av[1], d + 1))
            elif op is C.ATOMIC_GROUP:
                stack.append((av, d + 1))
            elif op is C.GROUPREF_EXISTS:
                stack.append((av[1], d + 1))
                if av[2] is not None: stack.append((av[2], d + 1))
            elif op is C.BRANCH:
                for b in av[1]: stack.append((b, d))
            elif op in (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT):
                stack.append((av[2], d))
    return best

TOKENS = ["(", ")", "(?:", "(?#", "(?x)", "(?x:", "(?-x:", "(?i)", "[", "]", "^", "\\", "\\(", "\\)", "\\[", "\\]",
          "#", "\n", "a", "|", "*", "(?=", "(?<!", "(?>", "(?P<n>", "(?(1)", " ", "[^", "-", "{2}", "+"]
rng = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
checked = under = 0
worst = []
for _ in range(int(sys.argv[2]) if len(sys.argv) > 2 else 200000):
    p = "".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 40)))
    try:
        tree = P.parse(p)
    except (re.error, RecursionError, OverflowError):
        continue
    checked += 1
    t, s = true_depth(tree), group_depth(p)
    if s < t:
        under += 1
        if len(worst) < 5: worst.append((p, t, s))
print(f"checked={checked} under_counts={under}")
for w in worst: print(repr(w))
