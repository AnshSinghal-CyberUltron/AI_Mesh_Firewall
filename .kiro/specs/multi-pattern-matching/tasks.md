# Task 7 — tasks

- [x] 7.0 Refute the cheap alternative first (combined `re` alternation: 0.9×, slower)
- [x] 7.1 R2 gate: prefilter never under-reports — 0 of 762 inputs
- [x] 7.2 Measure the realistic ceiling: 7.4× matching path (not the 788× headline)
- [x] 7.3 Identify what cannot be ported and why: 7 lookaround FP-suppressors
- [x] 7.4 `_candidate_keys` prefilter + one guard line per family loop
- [x] 7.4b **Build the database at startup, not lazily.** MEASURED 326 ms to compile 56
      patterns, once per process. Lazily it lands on the FIRST REQUEST through each fresh
      worker — a 326 ms cold-start spike hidden behind a healthy p50, repaid on every worker
      recycle and scale-out event. Now built in the startup hook, before traffic.
- [x] 7.5 Corpus equivalence: 748/748 byte-identical, prefilter on vs off
- [x] 7.6 Test: `_PREFILTER = None` reproduces identical output — the fallback IS this code with the guards inert
- [x] 7.7 Test: the 7 re-only FP-suppressors asserted absent from the prefilter set; a broken prefilter still redacts
- [x] 7.8 Full suite without hyperscan (the container's reality): **240 → 240, zero new**; with-hyperscan arm running
- [ ] 7.9 E2E `--repeat`: CPU/request and p99 vs ~27.3 ms / 26–41 ms
