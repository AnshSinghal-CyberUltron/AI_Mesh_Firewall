# Task 8b — one `lru_cache` decorator halves the gateway's CPU per request

**Tag `[M]`** · A/B on the **same image**, one variable (the `lru_cache` decorator on the
`redact_all` body), concurrency 16, `--repeat 3 --settle-s 10`, **`full` trace mode**
(the shipped default), 45 policies, Tier-2 off, gateway 4 vCPU

## The controlled comparison

| | cache **OFF** (control) | cache **ON** (8b) | attributable |
|---|---:|---:|---:|
| firewall tax p50 | 8.50 ms | **7.50 ms** | −12% |
| p90 | 55.1–57.6 ms | **10.0–11.2 ms** | **~5×** |
| p99 | 145.5–149.4 ms | **26.4–40.8 ms** | **~4×** |
| achieved RPS | 26.7 | **29.7** | +11% |
| gateway CPU | 1.60 cores | **0.81 cores** | −49% |
| **CPU per request** | **59.9 ms** | **27.3 ms** | **halved** |

Three runs per arm, tight in both (p90 spread 2.5 ms and 1.2 ms).

**Why the A/B was necessary:** the historical baseline (p90 54.10–56.80, p99
100.90–151.20) was measured before task 7's GC instrumentation and the off-stage timing
fields were added, so a before/after against it could not attribute the win. The control
arm lands almost exactly on that baseline, which both validates it and rules out anything
else between task 6 and now.

## What one decorator is doing

`redacted_prompt` is `None` whenever no redaction fired (`main.py:7952`), so the allow path
runs

```python
prompt=_trace_text(prompt)
forwarded_prompt=_trace_text(redacted_prompt or prompt)   # the SAME string
```

— redacting identical text twice per request, on the common clean path, in both trace
modes. `redact_all` is **78% of on-CPU time** (py-spy, 121,169 samples) at ~8.19 ms per
pass (the plan's own G4.4 figure).

## Safety

Byte-identical output, proven rather than argued from purity:

| check | result |
|---|---|
| 748 corpus items, cached vs uncached | identical |
| 8 adversarial inputs (zero-width, Unicode tag-block, long API keys, Bearer, aws secret, cards, SSNs, empty) | identical |
| `redact_all_scoped` excluded from the text-keyed cache | asserted, both call orders |
| cache bounded | asserted |
| full gateway suite | 240 → 240, **zero new** |

The scoped exclusion is the load-bearing one. A text-keyed cache is shared across tenants;
that is sound for `redact_all`, whose output depends on nothing but the text, and **unsound
for `redact_all_scoped`**, whose output depends on `allowed_classes` — one scope would
answer for another and mask a class the operator explicitly chose not to mutate.

## A note on the attribution method

The control arm's own tail attribution reads **93% of a +91 ms excess inside the
per-request stage sum**. The sum-of-per-stage-medians method this session started with
would have reported ~3% and called it "outside every stage" — the false reading that cost
several iterations before `stage_latency_sum_ms` exposed it.

## Position against the goal

| | session start | now |
|---|---:|---:|
| p50 | 8.2 ms | **7.5 ms** |
| p90 | 51.6–56.8 ms | **10.0–11.2 ms** |
| p99 | 103–158 ms | **26.4–40.8 ms** |
| CPU/request | ~56 ms | **~27.3 ms** |
| RPS/vCPU (derived from CPU/request) | ~18 | **~37** |

p99 32.60 ms against a 20 ms bound: **1.6× short, from 7.5×**. The driver still refuses to
publish an RPS number, correctly, for the ninth time.

**Streaming remains unmeasured** since task 1E (358–962 ms first-token, before any of this
work). `_redact_trace_text` runs on that path too, so 8/8b plausibly help — that is a guess
until measured.
