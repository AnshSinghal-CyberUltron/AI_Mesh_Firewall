# Tasks

- [ ] **T1** `Finding` dataclass + `outcome`, `findings`, `detectors_run` on `PipelineDecision`.
- [ ] **T2** Derive `outcome` from enforced findings; `monitor` never an outcome.
- [ ] **T3** `resolve_and_enforce` populates findings for enforced AND observed rules.
- [ ] **T4** Policy-engine findings carry `enforced` per rule (monitor rules → False).
- [ ] **T5** Tests: a monitor finding SURVIVES a co-occurring redact; outcome stays `redact`.
- [ ] **T6** Tests: R5 — a monitor injection rule leaves PII rules enforced.
- [ ] **T7** Tests: R6 — zero policy ⇒ `allow` + empty findings, native and OpenAI-SDK.
- [ ] **T8** `as_dict` emits both; existing `action` unchanged (R7 regression test).
- [ ] **T9** Separate task: `stage_latency_ms` → float, and emit 0 distinctly from absent (G1.4).
