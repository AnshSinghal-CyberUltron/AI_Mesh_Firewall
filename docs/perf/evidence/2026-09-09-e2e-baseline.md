# First end-to-end nine-stage baseline — Docker, dev/perf-9stage

**Stack:** project `aimeshperf`, `docker-compose.yml` + `scripts/perf/e2e/compose.perf.yml`
**Host:** Intel Xeon Platinum 8581C @ 2.30 GHz · gateway pinned to 4 CPUs / 6 GiB, `WEB_CONCURRENCY=4`
**Posture:** `enforcement_mode=block`, Tier-2 OFF, **45 policies loaded** (`policy_cache_version: 8`)
**Upstream:** in-gateway loadtest stub, 2 s × 100 tok/s = **200 tokens/answer**
**Driver:** `scripts/perf/e2e/drive.py`, unique prompts, 4,096-char band
**Tag:** `[M]` — measured over real HTTP against running containers

## 1. Non-streaming — the number stands

| metric | p50 | p90 | p99 |
|---|---:|---:|---:|
| wall | 2077.72 | 2084.38 | 2093.79 |
| total (trace) | 2015.00 | 2016.20 | 2019.20 |
| **FIREWALL TAX** | **14.80** | **16.00** | **19.00** |
| ├ addon_pre | 6.40 | 7.20 | 7.30 |
| ├ addon_post | 8.30 | 9.10 | 13.00 |
| └ tier2 | 0.00 | 0.00 | 0.00 |

| stage | p50 ms |
|---|---:|
| **output_guardrail** | **8.30** ← dominant firewall stage |
| model_routing | 1.70 |
| rate_limit | 0.80 |
| auth | 0.40 |
| kill_switch | 0.20 |
| policy | 0.20 |
| model_input | 0.20 |
| **input_scan** | **0.10** |
| model_output (stub) | 2000.10 |

Honesty checks: 200 tokens/sample · reconciliation residual **0.000 ms** · 23 unique prompts ·
`input_scan` and `output_guardrail` both non-zero. A repeat run gave 15.00 ms p50.

## 2. The number is real, but read it with its posture

**14.80 ms p50 is already inside the 20 ms target — and that is not a performance achievement.**

`input_scan` costs **0.10 ms** because `_scan_prompt_sync` is a passthrough since the
`policy-driven-detection` rewrite (`scanner.py:1160`). The built-in Tier-1 families were deleted, and
of the 45 seeded policies only the `pipeline` domain (16 of 57 in the package; the rest are rag/mcp/
vector) applies to chat. So the pipeline is fast largely because it does little Tier-1 detection —
the "fail-toward-no-detection" posture the scanner's own docstring describes.

The honest statement is therefore:

> ≤20 ms is met today at 4,096 chars, non-streaming, block posture, 45 policies, Tier-2 off —
> **with input_scan at 0.10 ms.** Restoring detection will consume budget, and the measured
> policy-engine slope (~0.065 ms/rule, or ~0.028 ms/rule after the thread fix) says how much.

The optimisation work in this spec remains necessary: it buys the headroom that restored detection
will spend.

## 3. Streaming is NOT measurable yet — attribution defect

A streaming run reported a **2,638 ms p50 "firewall tax"**. It is not real:

| stage | p50 ms |
|---|---:|
| model_output | **0.00** ← but 200 tokens were emitted |
| output_guardrail | 446.70 |
| addon_pre | 2184.20 |
| overhead | 2180.70 |

The tax is defined as `total − model_output`. On the streaming path `model_output` is **0 while the
upstream demonstrably produced 200 tokens**, so there is nothing to subtract and the provider's
generation time is attributed to the gateway.

This is the same **class** of defect as `addon = TTFT`. `honest-stream-latency-metric` fixed the
*anchor* (addon no longer collapses onto TTFT, guarded by
`test_requirement8_synthetic_timing_addon_tracks_gateway_not_ttft`), but `model_output`
**attribution** on the streaming path is a separate hole and is still open.

### My own harness missed it first

The initial `drive.py` printed that 2,638 ms and said *"All honesty checks passed."* It verified
tokens > 0 and that scan stages were non-zero, but never that **`model_output` is non-zero when
tokens were emitted** — the one invariant that catches this.

Fixed: the driver now refuses with exit 1 —

```
*** RUN REFUSED ***
  - upstream emitted tokens (p50 200) but stage 'model_output' is 0 ms on every
    sample. The firewall tax is total - model_output, so provider time is being
    attributed to the gateway and the reported tax (2636.8 ms p50) is NOT the
    gateway's. Refusing to report it.
```

Non-streaming still passes (15.00 ms p50), so the new check is specific, not blanket.

**Consequence:** no streaming latency figure may be quoted until the `model_output` attribution is
fixed. Added to the tasks file.

## 4. Reproduce

```bash
docker compose -p aimeshperf -f docker-compose.yml -f scripts/perf/e2e/compose.perf.yml up -d
docker exec -i aimeshperf-control-1 sh -c 'cd /app/control && ./.venv/bin/python manage.py shell' \
  < scripts/perf/e2e/bootstrap_org.py          # prints PERF_API_KEY=...
python scripts/perf/e2e/drive.py --key $PERF_API_KEY --n 20 --prompt-chars 4096
```

---

# Task 1A resolved — streaming is measurable, and it is ~10× worse

## Root cause (corrects §3)

§3 called this "the same class as `addon = TTFT`" and implied a production hole. **That
characterisation was too strong.** The root cause is specific to the *stub* path:

`_rebuilt_stream_trace` (`stream_orchestration.py:682`) sets `model_output_ms` only when

```python
if metrics.ttft_ms > 0 and metrics.duration_ms > metrics.ttft_ms:
```

`first_token_ts` is set by `_track_chunk` in `llm_router.acompletion_stream` — but the loadtest-stub
branch `return`s **before** `_track_chunk` is wired, and `loadtest_stub_stream` is a plain generator
with no access to `local_metrics`. So on the stub path `ttft_ms` stayed 0, `model_output_ms` was never
set, and the tax absorbed the whole generation.

A **real provider** goes through `_track_chunk`, so the production path is very likely unaffected.
This was a harness artifact. Recording the over-call so the correction is on the record.

## Fix

`llm_router.py` — the stub branch now records `first_token_ts` on its first content frame, exactly as
`_track_chunk` does for a real provider. Six lines, with the reasoning inline.

## Second finding: stages OVERLAP on streams, so additive reconciliation is invalid

After the fix, `model_output` populated (2,589 ms) but reconciliation failed with a **407 ms residual**
against a 25 ms epsilon — on data whose individual stage values were correct.

`output_guardrail` scans each chunk **while the provider is still generating**, so its time is
concurrent with `model_output`. Summing them double-counts. The additive invariant holds for
non-streaming and is simply wrong for streaming; applying it there rejects good data.

The driver now splits the two quantities:

| quantity | meaning |
|---|---|
| **ADDED WALL CLOCK** = `wall − model_output` | what the caller actually waited beyond generation — **the honest streaming tax** |
| `guard_accum` = `addon_post` | accumulated guard `inspect()` time; **concurrent work, not added latency** |

## Measured, both modes passing (exit 0)

| mode | metric | p50 | p90 | p99 |
|---|---|---:|---:|---:|
| non-streaming | firewall tax | **15.20** | **20.50** | **20.60** |
| streaming | **added wall clock** | **148.89** | 151.50 | 151.75 |
| streaming | guard_accum (concurrent) | 446.80 | 464.60 | 469.50 |

## What this changes

1. **Streaming is ~10× worse than non-streaming** — 148.89 ms vs 15.20 ms added. Streaming is the
   default for chat, so **this is the real optimisation target**, not the non-stream path.
2. **Non-streaming already breaches 20 ms at p90** (20.50) and p99 (20.60). The p50 headline of
   15.20 ms is inside target; the tail is not.
3. The 8.30 ms non-stream `output_guardrail` and the 446.80 ms streaming `guard_accum` are the same
   control priced differently: per-chunk scanning over ~200 tokens costs far more than one pass over
   the finished answer.
4. Earlier plan work assumed the classifier and Tier-1 dominate. **On this tree, with Tier-1 a
   passthrough and Tier-2 off, the output guard dominates both modes.** The optimisation order in
   `tasks.md` should be revisited against this evidence before task 2 starts.
