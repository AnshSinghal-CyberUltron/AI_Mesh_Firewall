"""Cross-check §9.5 UI task dependencies (and T-refs in §9 card bodies) against §10.12 T-task dispositions."""
import re, sys
rb = open(sys.argv[1], encoding="utf-8").read().split("\n")
ui = {}
for i in range(1432, 1448):  # §9.5 table rows (1-based 1433..1447)
    m = re.match(r"\| (UI\d\d) \| [^|]+\| ([^|]+)\|", rb[i])
    if m:
        ui[m.group(1)] = (i + 1, m.group(2).strip())
disp = {}
for i in range(3335, 3364):  # §10.12 rows (1-based 3336..3363)
    m = re.match(r"\| (T\d\d) [^|]*\| ([^|]+)\|", rb[i])
    if m:
        disp[m.group(1)] = (i + 1, m.group(2).strip())
def expand(dep):
    out = []
    for part in [p.strip() for p in dep.split(",")]:
        m = re.match(r"T(\d\d)\s*[-–]\s*T(\d\d)", part)
        if m:
            out += [f"T{n:02d}" for n in range(int(m.group(1)), int(m.group(2)) + 1)]
        elif part.startswith("T"):
            out.append(part)
    return out
print(f"parsed UI rows={len(ui)} T dispositions={len(disp)}\n")
for u, (ln, dep) in ui.items():
    ts = expand(dep)
    if not ts:
        continue
    print(f"{u} (rb.md:{ln}) depends on: {dep}")
    for t in ts:
        dl, d = disp.get(t, (None, "NOT IN §10.12"))
        kind = "RETAINED" if d.startswith("Retained") else ("SUPERSEDED" if d.startswith("Superseded") else ("ABSORBED" if d.startswith("Absorbed") else ("ELEVATED" if d.startswith("Elevated") else "?")))
        gws = sorted(set(re.findall(r"GW\d\d", d)))
        print(f"   {t}: {kind:10s} -> {','.join(gws) or '-':12s} (rb.md:{dl}) {d[:90]}")
