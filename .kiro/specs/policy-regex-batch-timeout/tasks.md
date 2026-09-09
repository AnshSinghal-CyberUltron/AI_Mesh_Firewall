# Tasks

- [ ] **T1** Add `_search_many_with_budget(jobs)` — one worker round-trip for a list of
      (key, compiled, text) triples, returning `{key: bool}`. Per-job exception → False.
- [ ] **T2** Split `evaluate()` into resolve/decide passes; `_evaluate_rule` gains an
      optional precomputed-verdict map. Default `None` keeps the old path for every other
      caller (`evaluate_mcp_policies`, `evaluate_for_stage`).
- [ ] **T3** Fallback: on batch timeout, re-run every regex rule through the existing
      per-rule path. No new timeout logic.
- [ ] **T4** Equivalence gate: for a corpus of inputs, assert the full `EvaluationResult`
      (matched ids, action, hints, message) is identical batched vs unbatched. This is R1;
      it must run against the real bundle, not a toy one.
- [ ] **T5** Re-run `bench_policy_engine.py` — expect the handoff line to collapse.
- [ ] **T6** Re-run the E2E load harness; compare the policy stage and `overhead_ms`
      against the 8.80 ms / 11.70 ms baseline. Report whether R4's prediction held.
- [ ] **T7** Record the result — including if the prediction was wrong.
