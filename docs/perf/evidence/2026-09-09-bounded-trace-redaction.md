# Trace redaction is now bounded by the trace limit, not the prompt

## What changed

`pipeline_trace._truncate(text, limit=1200)` redacted the **whole** text, then kept 1200
characters. `redact_all` is linear at ~1.2 µs/char, so a diagnostic artefact cost grew
without bound with prompt length. Measured at **39.6% of firewall CPU** — more than the
policy engine spends deciding anything.

It now redacts a **window** ending at whitespace.

## Result — flat, as required

Same output, byte for byte, at every size:

| prompt | window | before | after | |
|---|---|---|---|---|
| 1200 | 1200 | 0.77 ms | 0.77 ms | 1.0× |
| 2048 | 1461 | 1.29 ms | 0.94 ms | 1.4× |
| 4096 | 1461 | 2.52 ms | **0.98 ms** | 2.6× |
| 8192 | 1461 | 5.01 ms | **0.93 ms** | 5.4× |
| 16384 | 1459 | 9.97 ms | **0.94 ms** | 10.6× |
| 32768 | 1459 | 30.25 ms | **0.94 ms** | 32.1× |

The point is not the multiplier — it is that the "after" column is **flat**. Cost is now a
function of the trace limit, which is what R3 asked for.

## Why a whitespace boundary is the sound cut

Redacting after truncating would be unsound: a secret cut in half may no longer match its
pattern, leaving the visible half in the trace. That is why the original redacted everything.

Auditing every redaction pattern for unbounded length splits them cleanly:

- **Unbounded patterns match a whitespace-free run** — `xox[baprs]-[0-9A-Za-z-]{10,}`,
  `glpat-[A-Za-z0-9_-]{20,}`, `(?:mongodb|postgres|…)://[^\s]{10,}`. Such a run **cannot
  straddle a cut placed at whitespace**.
- **Patterns containing whitespace are short** — SSN ~11 chars, phone ~14, card ~19,
  `-----BEGIN PRIVATE KEY-----` ~30 (it matches the *header*, not the key body). All fit
  inside the 256-char overlap.
- **`LABEL<ws>SECRET` shapes** (`Bearer\s+…`, `password\s*[:=]…`) are the interesting case.
  If the cut lands between label and token the match fails — but what stays visible is the
  **label**, not the secret. The token is whitespace-free, so it lies wholly on one side.

## Residual risk, stated

A single **whitespace-free run longer than HARD_CAP (4096)** that *spans* the cut ends the
window mid-run, so a pattern matching that run is missed. Bounded, named, tunable, and
pinned by a test. A long token entirely before the limit is inside the window and masked
normally.

## Verification

- **Equivalence gate**: 1377 inputs — detection corpus, plus **every secret shape planted at
  every offset across the cut** (which is exactly where a windowing bug lives), plus long
  runs, unicode, newline floods, empty. **Byte-identical.**
- **22 unit tests** pinning the soundness argument itself, not just the happy path: the cut
  lands on whitespace, a 1500-char token spanning the naive cut is wholly inside the window,
  a run > HARD_CAP stops at the cap, PII before the limit is still masked.
- Enforcement unchanged end-to-end: block → 400, redact → 6 rules, allow → clean; trace text
  still masked (`a***@b***.com`, `****-****-****-11`).

## CPU per request

Measured with an **idle baseline subtracted** (the container burns 4.98 ms CPU per
wall-second at rest, 0.5% of a core), 60 requests, scan-only path, unique prompts:

| | wall | CPU/req | RPS/vCPU |
|---|---|---|---|
| before (no hyperscan, full-text redaction) | 13.47 ms | 13.25 ms | 75.5 |
| **after** | 11.43 ms | **11.11 ms** | **90.0** |
| after, 16 KB prompt | 31.05 ms | 29.52 ms | 33.9 |

**A methodology correction**: one intermediate reading gave 22.25 ms CPU/req against 11.55 ms
wall. CPU above wall on a single-threaded request is impossible, which is what flagged it —
the container-wide counter had picked up background work left over from the profiling
session. Subtracting a measured idle baseline and widening the window to 60 requests makes
gross and net agree (11.17 vs 11.11), and wall and CPU agree. The 22.25 figure was noise,
not a regression.

The 16 KB row shows where the next cost is: **the policy engine also scales with prompt
length** (92 regexes over the full text). Trace redaction is now flat; detection is not.
