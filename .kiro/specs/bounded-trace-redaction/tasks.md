# Tasks

- [ ] **T1** `_redaction_window(text, limit, overlap, hard_cap)` in `pipeline_trace.py`.
- [ ] **T2** `_truncate` redacts the window instead of the whole text.
- [ ] **T3** Equivalence gate: corpus + secrets planted at every offset across the cut,
      long whitespace-free runs, unicode, empty/whitespace-only. Byte-identical or fail.
- [ ] **T4** Unit tests pinning the soundness argument itself: a token-shaped secret
      straddling the cut, a `Bearer <token>` split by the cut, a run > HARD_CAP.
- [ ] **T5** Measure: `redact_all` cost for the trace vs prompt length — must be flat (R3).
- [ ] **T6** E2E A/B, and record whether the CPU/request figure moves as predicted.
