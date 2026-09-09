# Task 6 — design

## `GATEWAY_PIPELINE_TRACE_MODE`

| value | behaviour |
|---|---|
| `full` (**default**) | exactly today — byte-identical trace, no code path changed |
| `metrics` | stage `name` / `action` / `latency_ms` / `threat_type` / `category`, plus the root timing keys; every text payload dropped |

One helper, `_project_pipeline_trace(trace)`, applied at each allow-path attach site
(`:2876`, `:9211`, `:11109`) and the stream terminal frame. In `full` mode it returns its
argument unchanged — not a copy — so the default path costs one env lookup and nothing
else.

## What `metrics` keeps, and why exactly this set

R2 fixes the floor: the harness needs stage **names** (nine-stage assertion), per-stage
**latency_ms** (tail attribution), and the root `t_addon_pre_ms` / `t_addon_post_ms` /
`total_ms` / `overhead_ms` (the firewall tax and its reconciliation). `action`,
`threat_type` and `category` are kept because they are small and are what makes a trace
diagnostically useful at all.

Dropped: `content`, `prompt_in`, `prompt_out`, `prompt_submitted`, `prompt_preview`,
`input_text`, `input_text_before`, `input_text_after`, `output_text`, `final_response` —
the text duplication that is 98% of the bytes.

## Verification

1. A test that `full` is byte-identical to no projection at all.
2. A test that `metrics` keeps every field the harness reads, and drops every listed text
   field — so a future edit cannot quietly remove a field the honesty checks depend on.
3. A test that projection never introduces a key absent from the input (R3).
4. Sweep in both modes; report the difference, including if there is none.
