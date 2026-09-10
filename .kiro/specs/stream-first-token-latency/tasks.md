# Task 1E — tasks

- [x] 1E.1 Equivalence harness over the corpus, built and run BEFORE the change
      (`scripts/detection/stream_retention_equivalence.py`)
- [x] 1E.2 Snapshot current released-bytes + actions (748 items; plus a `--long`
      mode embedding each item in ~1500-char prose, 2244 documents, because no
      corpus item exceeds 93 chars and none would reach the BUFFER_LIMIT trigger —
      the gate would have been vacuous for the regime that matters)
- [x] 1E.3 Content-derived `min_retain` (`_min_retain_bytes`) + byte-denominated
      flush trigger (`STREAM_FLUSH_BYTES`)
- [x] 1E.4 R2 gate: 748/748 and 2244/2244 byte-identical, every enforced verdict
      unchanged. 5 long documents changed guard-pass COUNT by +1 — cadence, not
      detection; the comparator now separates the two rather than conflating them
- [x] 1E.5 R1: first release 129 → 30 chunks; short answers now stream. Both
      strict-xfail ratchets flipped and their markers are removed
- [x] 1E.6 R6: `test_stream_flush_per_token.py` 3/3 — the 1C CPU win holds
- [x] 1E.7 Wider suite: 64 → 62 failures, **zero new**; the 2-test delta is this
      task's own ratchets going green
- [ ] 1E.8 Re-measure E2E HEAD in Docker; record before/after
