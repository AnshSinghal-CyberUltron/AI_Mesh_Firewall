# Task 8b — tasks

- [x] 8b.1 Corpus equivalence: 748 items byte-identical, cached vs uncached, plus 8
      adversarial inputs (zero-width, Unicode tag-block, long keys, Bearer, aws secret,
      cards, SSNs, empty)
- [x] 8b.2 Test: `redact_all_scoped` is NOT routed through the text-keyed cache — its
      output depends on `allowed_classes`, so caching on text alone would let one scope
      answer for another
- [x] 8b.3 `lru_cache(maxsize=64)` on the `redact_all` body, bounded
- [x] 8b.4 Full suite — 240 before, 240 after, zero new
- [ ] 8b.5 E2E in FULL mode. First attempt VOID (container-removal race left one of two
      runs with zero samples). Clean 3-repeat run: p90 10.0–11.2, p99 26.4–40.8,
      CPU/request ~27.3 ms vs a ~56 ms baseline — but that baseline predates task 7's GC
      instrumentation and the off-stage timing fields, so **an A/B on the same build,
      cache on vs off, is running before the win is attributed to 8b**
