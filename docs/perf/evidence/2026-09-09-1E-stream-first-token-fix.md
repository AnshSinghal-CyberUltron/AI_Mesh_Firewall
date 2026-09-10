# Task 1E — streaming first-token latency: 3.2–4.0× faster, detection unchanged

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, streaming,
in-gateway stub 100 tok/s **duration 3 s** (matched to the 1D baseline), gateway 4 CPU /
`WEB_CONCURRENCY=4`, n=10 + 2 warmup per point

## Result

| tokens | HEAD before | **HEAD after** | speedup | guard_accum before | after |
|---:|---:|---:|---:|---:|---:|
| 100 | 3097.80 ms | **962.33 ms** | **3.2×** | 6.50 ms | 6.80 ms |
| 200 | 2042.72 ms | **509.95 ms** | **4.0×** | 17.30 ms | 14.90 ms |
| 300 | 1393.31 ms | **358.13 ms** | **3.9×** | 24.50 ms | 23.50 ms |

Guard CPU is unchanged within noise — the 31.9× win from 1C is fully preserved while
first-token latency drops 3–4×.

The qualitative change matters more than the ratio. At 100 tokens the **token span**
(first→last token at the client) went **59.50 ms → 2196.18 ms**: the answer used to
arrive as one blob at `[DONE]`, and now flows across the whole generation. It is a
stream again.

`ADDED WALL CLOCK` barely moved (139.81 → 138.41, 147.51 → 141.65, 144.77 → 142.01),
confirming 1D's finding that it was never the metric carrying the defect.

## The change

Two things in `secure_streaming.py`, both denominating a limit in the unit it actually
governs:

1. **`_min_retain_bytes`** — retention was a flat `STREAM_LOOKAHEAD_BYTES` (512). It is
   now `min(512, trailing_non_whitespace_run + 64)`. 512 stays a hard **cap**, so no
   input is ever held longer than before; in prose it drops to ~70 bytes.
2. **`STREAM_FLUSH_BYTES = 160`** — the non-final flush trigger was 64 *chunks*, which
   for token-sized deltas is 384 bytes. It is now denominated in bytes, so cadence no
   longer depends on the provider's delta size.

### Why 64, and why the old 512 was never a bound

Measured over all 166 pattern strings in `patterns.py` via the regex parser's
`getwidth()`:

- 31 patterns can match across whitespace;
- **25 of those are UNBOUNDED** (`[\s\S]*?`, `{20,}`, `.*?`) — no finite window covers
  them, and the old flat 512 did not either;
- the widest bounded one with whitespace *inside* the match is `phone_intl` at 52 chars.

So 512 over-covered the bounded patterns by 10× while under-covering 25 unbounded ones.
Contiguous tokens (keys, emails, URLs) are covered exactly, since the trailing
non-whitespace run is retained whole up to the cap. The stale comment claiming 512
"must exceed the longest single PII/secret token" is corrected in place — even the
bounded `email` pattern reaches 601 chars.

### Choosing the flush threshold

Measured Pareto over 300 token-sized deltas:

| FLUSH_BYTES | first release @chunk | guard passes |
|---:|---:|---:|
| 64 | 25 | 29 |
| 96 | 19 | 21 |
| **160** | **30** | **13** |
| 192 | 35 | 11 |
| 384 | 65 | 6 |

160 is the largest threshold still inside the ≤32-chunk budget from R1.

## Detection equivalence — the gate, run before the change

`scripts/detection/stream_retention_equivalence.py` streams every corpus item through
the real `SecureStreamingResponse` in 6-byte deltas with a guard whose verdict comes
from the gateway's own `detect_pii`, and records what the client actually receives.

| corpus | items | result |
|---|---:|---|
| `tests/detection_corpus/` | 748 | **byte-identical**, every enforced verdict unchanged |
| same, embedded in ~1500-char prose (`--long`) | 2244 | **byte-identical**, every enforced verdict unchanged |

**The corpus alone would have been vacuous.** No item exceeds 93 chars, so none reaches
the `BUFFER_LIMIT` trigger — the regime the defect lives in. Hence the `--long` mode.

Five long documents changed the *count* of `allow` guard passes by +1. That is flush
cadence, not detection: released bytes are identical. The comparator now separates
"released bytes or an enforced verdict changed" (fail) from "guard-pass count changed"
(reported). A verdict flipping from redact/block to allow necessarily changes the
released bytes, so bytes remain the real gate.

## Regression

| check | result |
|---|---|
| First-release ratchets | both flipped; strict-xfail markers removed |
| `test_stream_flush_per_token.py` (R6, the 1C win) | 3/3 |
| `e14_streaming_split` + `secure_streaming` + `stream_failclosed_parity` | 38/38 |
| Wider `stream or flush or e13 or e14 or guard or secure` | 64 → **62**, zero new; the 2-test delta is this task's own ratchets going green |

## A confound I caught, and the guard added for it

The first "after" run looked better than it was: the gateway rebuild reset
`GATEWAY_LOADTEST_STUB_DURATION_S` from 3 to the compose default of 2, so the stub paced
100 tokens over 2 s instead of 3 s. HEAD fell for a reason unrelated to the gateway, and
the 300-token point silently ran 200 tokens. Every number above is from a re-run at
duration 3, matching the 1D baseline. `drive.py` now refuses a run where
`--max-tokens` exceeds what the stub actually emitted, since that combination is both
shorter and faster-paced and is not comparable across runs.

## Out of scope, deliberately

`_release_redacted_with_lookahead_tail` — the branch taken when PII **is** detected —
still retains a flat 512 bytes. It only runs on streams already carrying detected
sensitive content, where being conservative costs little and correctness costs a lot.
Left unchanged; equivalence confirms it.

## Where streaming latency stands

First-token latency is **358–962 ms**, down from 1393–3098 ms. Non-streaming p50 is
**15.2 ms**. The remaining streaming figure is still far from 20 ms, and it is now
governed by one number: **~3.2 ms of fixed cost per guard pass**. The Pareto table shows
why that is the next lever — every remaining trade (flush cadence vs CPU) is priced by
it, so lowering it moves the whole curve rather than sliding along it.
