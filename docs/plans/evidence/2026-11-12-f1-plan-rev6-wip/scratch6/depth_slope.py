"""Frames per source nesting level, for every (opener, quantifier) unit: translate and regex.compile."""
import sys, re, regex, warnings, itertools
warnings.simplefilter("ignore")
sys.path.insert(0, "..")
import translate_prototype as T
from depth_probe3 import headroom, tdepth
OPEN = ["(", "(?:", "(?>", "(?i:", "(?-i:", "(?=", "(?!", "(?<=", "(?<!", "(?P<g{}>", "(?i:a|", "(a|", "(?:a|", "(?>a|", "(?(1)", "(?(1)a|", "(?=a|", "(?i:\\b|"]
QS = ["", "*", "*+", "*?", "?+", "{0,3}+"]
res = []
for o, q in itertools.product(OPEN, QS):
    if q and (o.startswith("(?=") or o.startswith("(?!") or o.startswith("(?<") or o.startswith("(?(")):
        continue
    hs = []
    for D in (8, 16):
        pre = "(a)" if "(?(" in o else ""
        s = "(?i)" + pre + "".join(o.format(i) for i in range(D)) + "a" + (")" + q) * D
        if o.startswith("(?<"):  # lookbehind needs fixed width
            s = "(?i)" + "".join(o.format(i) for i in range(D)) + "a" + ")" * D
        try:
            re.compile(s); t = T.translate(s, cpu_budget_s=None)
        except Exception as e:
            hs = None; break
        hs.append((headroom(lambda: T.translate(s, cpu_budget_s=None)), headroom(lambda: (regex.purge(), regex.compile(t))), tdepth(t)))
    if hs:
        st = (hs[0][0] - hs[1][0]) / 8; sc = (hs[0][1] - hs[1][1]) / 8
        res.append((sc, st, o, q, hs))
res.sort(reverse=True)
for sc, st, o, q, hs in res[:12]:
    print(f"{o+'…)'+q:16} frames/level: regex.compile {sc:5.2f} translate {st:5.2f}  (D=16 headroom compile {hs[1][1]}, translate {hs[1][0]}, tdepth {hs[1][2]})")
