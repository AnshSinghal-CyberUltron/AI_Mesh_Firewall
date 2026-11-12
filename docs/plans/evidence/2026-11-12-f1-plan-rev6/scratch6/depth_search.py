"""Random search for the source-nesting shape that needs the most stack in translate + regex.compile."""
import random, sys, re, regex, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T
from depth_probe3 import headroom, tdepth
D = int(sys.argv[1]); N = int(sys.argv[2]); R = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
OPEN = ["(", "(?:", "(?>", "(?i:", "(?s:", "(?-i:", "(?=", "(?!", "(?<=", "(?<!", "(?P<g{}>", "(?i:x|", "(x|", "(?:\\b|"]
CLOSE_Q = ["", "*", "*+", "*?", "?", "?+", "{0,3}+", "{0,2}"]
LEAF = ["a", r"(?i:[^\dı])", r"\b", r"[^\W\d]", r"(?i)k" if False else "k"]
worst = (10**9, None)
for k in range(N):
    opens = [R.choice(OPEN) for _ in range(D)]
    s = "(?i)" if R.random() < 0.3 else ""
    for i, o in enumerate(opens):
        s += o.format(i)
    s += R.choice(LEAF)
    for o in reversed(opens):
        q = "" if o.startswith("(?<") or o.startswith("(?=") or o.startswith("(?!") else R.choice(CLOSE_Q)
        s += ")" + q
    try:
        re.compile(s); t = T.translate(s, cpu_budget_s=None)
    except Exception as e:
        continue
    h = min(headroom(lambda: T.translate(s, cpu_budget_s=None)), headroom(lambda: (regex.purge(), regex.compile(t))))
    if h < worst[0]:
        worst = (h, s, tdepth(t)); print(f"headroom {h} tdepth {worst[2]} {s}", flush=True)
print("worst", worst)
