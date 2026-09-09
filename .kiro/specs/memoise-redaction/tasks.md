# Task 8b — tasks

- [x] 8b.1 Corpus equivalence: 748 items byte-identical, cached vs uncached, plus 8
      adversarial inputs (zero-width, Unicode tag-block, long keys, Bearer, aws secret,
      cards, SSNs, empty)
- [x] 8b.2 Test: `redact_all_scoped` is NOT routed through the text-keyed cache — its
      output depends on `allowed_classes`, so caching on text alone would let one scope
      answer for another
- [x] 8b.3 `lru_cache(maxsize=64)` on the `redact_all` body, bounded
- [x] 8b.4 Full suite — 240 before, 240 after, zero new
- [x] 8b.5 E2E in FULL mode, **A/B on the same image** (one variable: the decorator),
      3 runs per arm:
      | | cache OFF | cache ON |
      |---|---:|---:|
      | p90 | 55.1–57.6 ms | **10.0–11.2 ms** |
      | p99 | 145.5–149.4 ms | **26.4–40.8 ms** |
      | CPU/request | 59.9 ms | **27.3 ms** |
      | RPS | 26.7 | **29.7** |
      The first attempt was VOID (container-removal race, one arm with zero samples) and
      was discarded rather than reported. The control arm lands on the historical baseline,
      which validates it and rules out everything else changed since task 6.
      _Evidence: docs/perf/evidence/2026-09-09-task8b-AB-confirmed.md_
- [x] 8b.6 Streaming: the same fix pays out on a second path — **TAIL 34.69 → 4.45 ms
      (7.8×) at 100 tokens, 46.00 → 4.52 ms (10.2×) at 200**, because trace building runs
      after the response completes. First-token latency is unchanged, as predicted: it is
      set by task 1E's retention cap and flush threshold, not by CPU.
