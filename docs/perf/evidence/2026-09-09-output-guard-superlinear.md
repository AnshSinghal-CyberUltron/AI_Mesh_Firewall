# The output guard is super-linear in response length — task 1B

**Stack:** `aimeshperf` Docker, gateway 4 CPU / `WEB_CONCURRENCY=4`, 45 policies, block posture,
Tier-2 OFF, 4,096-char prompts, streaming · **Tag `[M]`**

## Measurement

Only the stub's token count varied (`PERF_STUB_DURATION_S` 1/2/3 at 100 tok/s); everything else fixed.

| tokens | guard_accum p50 | vs 100 tok | ADDED WALL CLOCK p50 |
|---:|---:|---:|---:|
| 100 | 112.5 ms | 1.00× | 150.51 ms |
| 200 | 454.6 ms | **4.04×** | 150.82 ms |
| 300 | 793.2 ms | **7.05×** | 152.38 ms |

Fitting `cost = k·nᵃ`: 100→200 gives **a = 2.01**, 100→300 gives **a = 1.78**. **a ≈ 1.9** — strongly
super-linear. Linear would have given 2× and 3×.

## Mechanism, from the code

Three independent flush triggers, not one:

| # | Trigger | Location | Default |
|---|---|---|---|
| 1 | `len(chunk_queue) >= max_buffer_chunks` | `secure_streaming.py:285` | **64 chunks** |
| 2 | `content_buffer_len >= buffer_max_bytes` | `:296` | 4,096 bytes |
| 3 | delta contains `.!?\n` | `:298` | — |

For the stub's `tok0 tok1 …` (no sentence punctuation, ~1.2 KB total) neither 2 nor 3 fires — **the
chunk-count limit (64) drives every flush**, giving `⌈n/64⌉` flushes.

And each flush scans the **cumulative** buffer:

```python
full_text = "".join(self._content_buffer)      # :329  — the WHOLE buffer
verdict = await self._output_guard.inspect(full_text, ...)   # :377
```

So total work ≈ `Σₖ (k · 64)` ≈ **O(n²/128)**, which is what the exponent shows. The staircase
(`⌈n/64⌉`) explains why the measured exponent sits slightly under 2.

## Why the added latency looks flat — and why that is not reassurance

ADDED WALL CLOCK held at ~150 ms across all three, because guard work runs **concurrently** with
generation and is hidden by it at these sizes. That hiding is arithmetic, not design:

- generation ≈ `10n` ms at 100 tok/s
- guard ≈ `112.5·(n/100)^1.9` ms

They cross at **≈1,200 tokens**, beyond which the guard becomes the critical path and added latency
starts climbing with the exponent. A 1,200-token answer is ordinary.

**The throughput cost is not hidden at all.** 454 ms of CPU per 200-token request is spent regardless
of whether it is visible in latency, and it caps RPS directly.

## Consequence: the optimisation order in the plan is wrong

Measured stage costs on this tree:

| stage | non-stream p50 | stream |
|---|---:|---:|
| **output_guardrail** | **8.30 ms** | **454.6 ms concurrent** |
| model_routing | 1.70 | 1.70 |
| rate_limit | 0.80 | 0.80 |
| policy | 0.20 | 0.30 |
| **input_scan** | **0.10** | **0.10** |

`tasks.md` task 2 targets the **policy engine** (thread-per-regex). The policy stage measures
**0.20 ms**. Even a perfect fix returns ~0.2 ms, while the output guard costs 8.30 ms non-stream and
454 ms of CPU on a 200-token stream.

**Task 2 is not the first lever. The output guard is.** The earlier ordering came from the 08-27 plan,
which was written when Tier-1 still ran the built-in scan at ~2,039 ms; that path is now a passthrough
(`scanner.py:1160`), so its share collapsed and the guard became dominant. The plan's ordering was
correct for the tree it was written against and is stale for this one.

### Proposed re-sequence (supersedes tasks.md ordering)

1. **Output-guard incremental scanning** — scan the *new* delta plus the retained lookahead, not the
   cumulative buffer. Turns O(n²) into O(n). Largest measured win in both modes.
2. **Flush-trigger review** — 64 chunks is aggressive for token-sized deltas; coalescing cuts flush
   count directly (and the 08-27 plan's SSE-coalescing egress lever wants the same change).
3. Policy-engine threads (old task 2) — keep, but as a smaller, later win now worth ~0.2 ms until
   detection is restored and the slope matters again.

**Do not start task 2 until this re-sequence is accepted**, or the quarter is spent optimising a
0.20 ms stage while a 454 ms one is untouched.

## Reproduce

```bash
for D in 1 2 3; do
  PERF_STUB_DURATION_S=$D docker compose -p aimeshperf \
    -f docker-compose.yml -f scripts/perf/e2e/compose.perf.yml up -d --force-recreate gateway
  python scripts/perf/e2e/drive.py --key $PERF_API_KEY --n 6 --prompt-chars 4096 --stream
done
```
