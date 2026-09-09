# Task 1D — the added streaming latency is at the HEAD, and it is huge

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, streaming,
in-gateway stub 100 tok/s, gateway 4 CPU / `WEB_CONCURRENCY=4`, n=12 per point

## What I was looking for, and what I found instead

1D set out to locate ~140 ms of "ADDED WALL CLOCK". Splitting that number into a HEAD
(request → first token at the client) and a TAIL (last token → last byte) showed the
question was wrong. The tail is small. The head is enormous.

| tokens | ADDED WALL CLOCK | **HEAD** | TAIL | token span |
|---:|---:|---:|---:|---:|
| 100 | 139.81 ms | **3097.80 ms** | 36.46 ms | 59.50 ms |
| 200 | 147.51 ms | **2042.72 ms** | 49.80 ms | 1205.53 ms |
| 300 | 144.77 ms | **1393.31 ms** | 51.67 ms | 1932.74 ms |

For a 100-token answer the caller waits **3.1 seconds** and then receives the whole
answer in a 59 ms burst. Nothing is streamed. The gateway turns a stream into a batch.

`ADDED WALL CLOCK` never showed this because the last byte *does* arrive promptly after
the upstream finishes. The metric was measuring the wrong end of the request.

## Why HEAD falls as the answer gets longer

3097 → 2042 → 1393 ms as tokens go 100 → 200 → 300. Latency improving with more work is
the signature of a fixed **chunk-count** threshold: the stub paces n tokens over a fixed
3 s, so a longer answer crosses a chunk threshold sooner in wall-clock terms.

Two hold-backs compose, and both are in `secure_streaming.py`:

1. **`_release_with_lookahead_tail` sets `min_retain = STREAM_LOOKAHEAD_BYTES`
   unconditionally** — the client is always ≥512 bytes (~86 token-sized chunks) behind,
   whatever the flush cadence. This exists to stop a PII token that is *starting* at the
   buffer edge from being half-streamed before a later scan can block it.
2. **A non-final flush only fires every `max_buffer_chunks` (64) chunks** (task 1C), so
   releases are quantised to 64-chunk steps.

Composed: the first byte leaves at the first multiple of 64 whose accumulated content
exceeds 512 bytes — **chunk 128** for ~6-byte tokens — or never, if the answer is
shorter than that.

### Predicted vs measured

Prediction from the model above, against the measured HEAD (stub interval = 3000/n ms):

| tokens | first release | predicted HEAD | measured | Δ |
|---:|---|---:|---:|---:|
| 100 | never (only 100 chunks) → `[DONE]` at 3000 ms | 3000 ms | 3097.80 | +98 |
| 200 | chunk 128 × 15 ms | 1920 ms | 2042.72 | +123 |
| 300 | chunk 128 × 10 ms | 1280 ms | 1393.31 | +113 |

The residual is a consistent ~100–120 ms — the pre-flight stages and upstream TTFT.
The model holds.

### Pinned deterministically

`test_stream_first_release_latency.py` measures the caller-visible property directly —
at which *input* chunk index the first byte comes out:

```
first byte released only after 129 of 300 input chunks
```

Predicted 128, measured 129. A 100-token answer releases nothing before `[DONE]`.

## My 1C fix regressed this, and I need to say so

Same test against the pre-1C commit (`c4cf82bd`), where the trigger fired on every chunk:

| | first release (300-token answer) | 100-token answer | guard CPU @300 tok |
|---|---:|---|---:|
| pre-1C | **chunk 89** | released before `[DONE]` | 793.2 ms |
| post-1C | **chunk 129** | nothing before `[DONE]` | 24.9 ms |

1C bought a 31.9× CPU reduction and cost ~40 chunks (~400 ms at 100 tok/s) of
first-token latency. It is a real trade, not a free win, and my 1C write-up did not
measure this side of it.

**I am not reverting 1C.** Reverting would spend 768 ms of CPU per request to buy back
400 ms of latency that task 1E removes outright — the 512-byte retention floor caps the
first release at chunk ~86 no matter how often the trigger fires. The trigger is the
minor term; the retention is the major one. Fixing them separately would churn the
trigger twice.

## Where the fix has to happen

Flush cadence alone cannot beat chunk ~86, because `min_retain` is a **floor**:

| flush threshold | guard passes (1800 B) | first release |
|---|---:|---:|
| every chunk (pre-1C) | ~237 | chunk 89 |
| every 64 B | 28 | chunk ~96 |
| every 128 B | 14 | chunk ~107 |
| every 64 chunks (today) | 3 | chunk 129 |

Every row is dominated by the 512-byte floor. The lever that matters is making retention
**content-aware** — retain the minimal suffix that could still extend into a match, with
512 as a *cap* rather than a floor — which would take the first release from ~86 chunks
to ~1–2.

That changes detection semantics and must not be done by inspection. It is task **1E**,
and it needs the requirements → design → review cycle plus the task-3 detection
equivalence gate as its safety net.

## Status of the <20 ms goal

Non-streaming p50 is **15.2 ms**. Streaming first-token latency is **1393–3098 ms**,
two orders of magnitude off, and the cause is now located and pinned by a strict-xfail
test rather than inferred.
