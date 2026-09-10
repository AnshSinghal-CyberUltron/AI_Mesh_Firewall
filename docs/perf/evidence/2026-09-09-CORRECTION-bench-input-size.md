# CORRECTION — the component measurement used a prompt 16× smaller than the real one

## What was wrong

`bench_policy_engine.py` used a **250-character** prompt. The load driver's
`--prompt-chars` defaults to **4096** (`load.py:299`), and `make_prompt` pads to exactly
that. Regex cost scales with input length, so every per-rule figure I reported was taken
against an input 16× smaller than the one the gateway actually sees.

## The same measurement, at the real size

| | 250 chars (reported) | 4096 chars (real) |
|---|---|---|
| `evaluate()` whole stage | 1890.9 µs | **7249.8 µs** |
| inline `.search()` | 426.7 µs (23%) | **6714.9 µs (93%)** |
| worker handoff | 1007.2 µs (**53%**) | **943.0 µs (13%)** |
| keyword rules | 42.1 µs | 188.1 µs |

The handoff cost is roughly constant per rule (11.19 µs → 10.48 µs) — it is a fixed
queue round-trip and does not care how long the text is. Matching cost is not: it grew
16× with the input, exactly as it should.

## What this corrects

**"53% of the policy stage is queue round-trips."** True at 250 characters. At the real
input size it is **13%**. The dominant cost of the policy stage is genuine regex matching:
6.7 ms of a 7.25 ms stage.

It also explains a discrepancy I had attributed to the wrong cause. I wrote that
`evaluate()` was 1.89 ms against a 8.80 ms stage and concluded "~7 ms of that stage was
always something other than rule evaluation". That was wrong. At the real input size
`evaluate()` alone is **7.25 ms**, which matches the measured 7.00 ms stage almost exactly.
**The policy stage is essentially all `evaluate()`.** There is no missing 7 ms; there was a
missing 3846 characters.

## What still stands

The batching change and its result are unaffected — that A/B was measured end-to-end
through the real driver at 4096 chars, so it never depended on the bench's prompt size:
p99 102.10 → 58.50 ms, policy tail 26.30 → 7.50 ms, policy outlier share 67% → 4%. What
changes is the *explanation* of its size: it removed 943 µs of fixed round-trip cost, not
1007 µs of a 1890 µs stage.

## What this reverses

I wrote that a literal/Hyperscan prefilter was "now clearly a *smaller* prize than it
looked". **The opposite is true.** 65 of 92 regex rules cannot match a benign prompt, and
at the real input size those 65 cost roughly `65/90 × 6714.9 µs ≈ 4.85 ms` — about
two-thirds of the entire policy stage. It is the largest single item measured so far.

## The lesson

A benchmark's input must be the input the system actually receives. I chose 250 characters
because it read like a plausible prompt, never checked it against the driver, and then drew
a conclusion about *which component dominates* — the one conclusion most sensitive to that
choice. `bench_policy_engine.py` now defaults to 4096 and names the driver flag it must
track.
