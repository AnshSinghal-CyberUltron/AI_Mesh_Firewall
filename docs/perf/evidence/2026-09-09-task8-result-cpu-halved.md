# Task 8 — CPU per request halved, p99 down ~4×

**Tag `[M]`** · concurrency 16, `--repeat 2 --settle-s 10`, metrics trace mode, scanner
pool 4, 45 policies, Tier-2 off, gateway 4 vCPU, idle host

## Result

| | before (task 6) | **after task 8** | change |
|---|---:|---:|---:|
| firewall tax p50 | 8.2 ms | **7.7 ms** | −6% |
| p90 | 51.6–52.4 ms | **10.7–19.6 ms** | **~3–5×** |
| p99 | 103–158 ms | **25.6–35.9 ms** | **~4×** |
| achieved RPS | 26.6–27.1 | **29.3** | +8% |
| gateway CPU | 1.53 cores | **0.81 cores** | −47% |
| **CPU per request** | **~56 ms** | **~27.5 ms** | **halved** |

Both runs reported, not the better one: `[2 runs: p90 10.7-19.6, p99 25.6-35.9]`.

The spec said the claim to test was a **CPU reduction**, with latency following only if
queueing was the mechanism. CPU halved and the latency followed — which also confirms the
queueing model in `…-untraced-cpu-is-7x-the-traced-cost.md`.

`gc_pause_ms` 0.69 → 0.59 and `telemetry_ms` 0.00 throughout: neither was ever involved.

## Still refused

> REFUSING TO REPORT A NUMBER: no concurrency level met p99 <= 20 ms with all nine stages
> running. Lowest p99 observed: 35.90 ms.

Closest any run has come. The bound is on the firewall tax, nine stages verified on every
sample, error rate 0.00%.

## What this does not fix

`metrics` mode only. **`full` is the default**, and there all four `redact_all` passes
still run — task 8 does nothing for the shipped configuration. That is what task 8b
addresses.

## Verification

- full gateway suite: 240 failures before, **240 after, zero new**
- 8 unit tests, including that the redaction *predicate*
  (`_redact_trace_text(raw) != raw`) answers identically in **both** trace modes — the
  regression a blanket change to that helper would have caused, reporting "nothing was
  redacted" about text full of PII
- a test pinning that the blanked keys are exactly those the projection drops, so adding
  one back to the kept set fails loudly rather than shipping an empty field

## Method note

Two process errors this round, both the same shape as one already corrected: `pkill -f`
and `pgrep -f` match the **invoking shell's own command line**. The `pgrep` case falsely
reported a busy host during a clean run; the `pkill` case killed its own parent shell. The
driver's host-idle check was fixed to match on argv; the ad-hoc command repeated the error
minutes later. One measurement was discarded for overlapping a stray suite run rather than
reported.
