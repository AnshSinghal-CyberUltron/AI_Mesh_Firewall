# Post-fix profile — redaction halved, and what is left is legitimate

**Tag `[M]`** · py-spy, live worker under concurrency-16 load, 25 s at 200 Hz,
idle/blocked frames excluded · compared against the pre-fix profile in
`…-ANSWER-untraced-cpu-is-trace-redaction.md`

| | before 8/8b | after 8/8b |
|---|---:|---:|
| total on-CPU weight | 13.3 | **8.4** (−37%) |
| `patterns.py` share | 74.54% | **56.68%** |
| `_redact_all_raw` | 43.16% | **31.74%** |
| `_iter_transport_decodes` | 13.54% | 10.26% |
| `socket.py` (real I/O) | 6.49% | **12.23%** |

Absolute redaction cost: 0.745 × 13.3 = **9.9** → 0.567 × 8.4 = **4.8** units, a ~52%
reduction — which matches the measured CPU/request halving (59.9 → 27.3 ms) arrived at
independently from `docker stats ÷ RPS`. Two methods, same answer.

I/O doubling *as a share* while total work fell is the expected shape when compute is
removed and socket work stays constant. `readinto` at 9.37% means real network cost is now
visible rather than buried.

## What remains is not waste

`redact_all` is still the largest single cost at 31.74%, but the calls that remain are the
ones whose output actually reaches a consumer — the operator UI in `full` mode, and the
audit log. The duplicate and the discarded work are gone.

Cutting further means one of two things, and they are different in kind:

1. **Redact less** — a detection-safety decision, not a performance one. It needs the
   corpus equivalence gate and an explicit argument about what the guard stops catching.
2. **Make the same redaction cheaper** — replace the regex engine (the plan's task 7,
   multi-pattern/Hyperscan). That task now has a *measured* justification: at 31.74% of
   on-CPU time it is the largest remaining item, where before this session it was assumed
   to be the policy engine.

## Position

| target | status |
|---|---|
| non-streaming p50 < 20 ms | **7.5 ms — met** |
| non-streaming p99 < 20 ms | 26.4–40.8 ms — 1.6× over |
| streaming tail | **~4.5 ms flat — solved** |
| streaming first-token | 332–934 ms — isolated to the 512-byte hold-back |
| RPS/vCPU | **~37 measured** (plans derived ~426 from the traced cost) |
