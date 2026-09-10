# Task 8 — tasks

- [x] 8.1 Test first: the redaction PREDICATE (`_redact_trace_text(raw) != raw`, used by
      `_trace_prompt_operator_masked`) is unaffected in BOTH modes — the regression a
      blanket change to that helper would have caused
- [x] 8.2 Test: `full` mode identical; the blanked keys are exactly those the projection
      drops, so re-adding one to the kept set fails loudly
- [x] 8.3 `_trace_text` helper applied at 13 allow-path sites; blocked path untouched
- [x] 8.4 Full suite — 240 before, 240 after, zero new
- [x] 8.5 E2E: **CPU/request 56 → 27.5 ms**, p99 ~4×, p90 ~3–5×, RPS +8% (metrics mode)
      _Evidence: docs/perf/evidence/2026-09-09-task8-result-cpu-halved.md_
