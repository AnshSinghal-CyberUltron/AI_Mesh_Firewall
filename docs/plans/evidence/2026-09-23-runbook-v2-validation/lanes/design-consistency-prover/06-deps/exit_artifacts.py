"""Each row: (consumer card, rb.md line, quoted substring that must appear on that line, producer card(s) of
the artifact the consumer's exit requires). Classified against the DECLARED graph:
IN-CLOSURE (fine) | MISSING (producer not a transitive dependency) | CYCLE (producer transitively depends on consumer)."""
import json, sys
from pathlib import Path
RB = Path(sys.argv[1]).read_text().splitlines()
G = {k: set(v) for k, v in json.load(open(Path(sys.argv[2]) / "declared_graph.json"))["cards"].items()}
def clo(x, s=None):
    s = set() if s is None else s
    for d in G.get(x, ()):
        if d not in s: s.add(d); clo(d, s)
    return s
NEEDS = [
 ("GW01", 2383, "Run the real-socket variant against v1 behind the staging nginx", ["T02"]),
 ("GW02", 2412, "Reuse staging/t02/ from the revamp branch", ["T02"]),
 ("GW02", 2423, "Score v1 against C3", ["T04"]),
 ("GW03", 2459, "Pools resize; no restart; no dropped in-flight request", ["GW06", "GW11", "GW12"]),
 ("GW04", 2494, "Run the full stage sequence in a deliberately shuffled order", ["GW05", "GW08", "GW11"]),
 ("GW04", 2496, "SKIPPED findings present and distinguishable from a clean EXECUTED result in the audit record", ["GW14"]),
 ("GW05", 2532, "Identical resolution on input and output and on all four surfaces", ["GW07", "GW13", "GW15", "GW16", "GW17", "GW18"]),
 ("GW05", 2536, "Save an unsupported detector/action combination in the console", ["UI07"]),
 ("GW06", 2572, "identical across quota, kill-switch and plan", ["GW05"]),
 ("GW07", 2608, "Zero provider calls; audit names the deciding rule and model version", ["GW10", "GW11", "GW14"]),
 ("GW07", 2611, "Output semantic detector UNAVAILABLE", ["GW10", "GW13"]),
 ("GW08", 2648, "Run the identical detection suite on all four backends", ["GW09", "GW10"]),
 ("GW08", 2653, "capacity_hint changes; admission bounds respond", ["GW19"]),
 ("GW09", 2680, "requires a ledger entry and a C3 score", ["GW02", "T04"]),
 ("GW09", 2687, "Recorder shows sanitized bytes; raw value absent from provider payload, logs, traces and error bodies", ["GW11", "GW12", "GW14", "T02"]),
 ("GW10", 2725, "Measure FPR at 1, 2, 4 and 7 windows on the benign corpus", ["T04"]),
 ("GW10", 2727, "Multilingual corpus", ["T04"]),
 ("GW11", 2764, "Recorder shows sanitized bytes; verification logged", ["GW09", "T02"]),
 ("GW11", 2765, "REWRITE with the local model", ["GW08"]),
 ("GW11", 2758, "Bound the connection pool from the ResourceContract", ["GW03"]),
 ("GW08", 2644, "Implement capacity_hint() so runtime/resources.py can derive guard-dependent bounds", ["GW03"]),
 ("GW12", 2803, "Provider and guard work stop within the bound", ["GW08"]),
 ("GW12", 2807, "Full conformance suite over real TCP", ["GW01", "GW15"]),
 ("GW12", 2808, "Matches the plan-derived schedule", ["GW13", "GW09", "GW10"]),
 ("GW13", 2843, "Output BLOCK with the semantic detector UNAVAILABLE", ["GW10"]),
 ("GW13", 2845, "Two separate profiles published", ["GW14"]),
 ("GW13", 2846, "Secret spanning a chunk boundary in both modes", ["GW09"]),
 ("GW14", 2880, "Recorder configured with 2,000 ms TTFT", ["T02"]),
 ("GW14", 2881, "Inject 5 ms auth, 5 ms input, 7 ms output delays", ["GW06", "GW08", "GW13"]),
 ("GW15", 2912, "the one shared-state round trip", ["GW06"]),
 ("GW15", 2920, "S01–S07 from §5 against v2", ["GW09", "GW10"]),
 ("GW15", 2923, "Replay 10,000 C2 chat requests through v1 and v2", ["GW02"]),
 ("GW19", 3070, "Offer 3× measured q_safe", ["GW20"]),
 ("GW19", 3074, "Guard service rate halved mid-load", ["GW08"]),
 ("GW23", 3360 - 0, "Runs on the v2 candidate before GW23", ["T23"]),
 ("GW24", 1169, "T08,T21,T24", ["T24"]),  # GW24 absorbs T25 (L3362); T25 depends on T24
]
# GW23 row: the requirement is stated in §10.12 (L3360) about T23; the consumer is GW23.
rows = []
for consumer, line, quote, producers in NEEDS:
    assert quote in RB[line - 1], (consumer, line, quote, RB[line - 1][:160])
    c = clo(consumer)
    for p in producers:
        kind = "IN-CLOSURE" if p in c else ("CYCLE" if consumer in clo(p) else "MISSING")
        rows.append((consumer, line, p, kind, quote))
w = max(len(r[4]) for r in rows)
for r in rows: print(f"{r[0]}  L{r[1]}  needs {r[2]:5s} -> {r[3]:10s}  \"{r[4]}\"")
from collections import Counter
print("\nsummary:", dict(Counter(r[3] for r in rows)))
print("CYCLE edges:", sorted({(r[0], r[2]) for r in rows if r[3] == "CYCLE"}))
print("MISSING edges:", sorted({(r[0], r[2]) for r in rows if r[3] == "MISSING"}))
