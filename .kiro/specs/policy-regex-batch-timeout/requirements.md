# Requirements — batch the policy engine's regex timeout handoff

## Context

Measured against the real 127-rule bundle (`scripts/perf/policy/bench_policy_engine.py`,
median of 200, single-threaded):

```
evaluate() WHOLE STAGE      1890.9 us
  92 regex rules            1829.5 us   (97% of the stage)
  35 keyword rules            42.1 us   ( 2%)
  INLINE .search() floor      426.7 us  ( 4.74 us/rule)
  VIA WORKER (current)       1434.0 us  (15.93 us/rule)
  HANDOFF OVERHEAD          1007.2 us  (11.19 us/rule)
```

Every regex rule hands its `search()` to a per-thread worker over a `SimpleQueue` and
blocks on a reply queue. **53% of the policy stage is that round-trip**, not matching.

Under load the stage costs 8.80 ms (trace, concurrency 16) against 1.89 ms here — a
**4.7× inflation**, consistent with a cost that is not CPU work: a queue round-trip
ping-pongs the GIL between two threads, and that worsens with concurrency, whereas
`re.search` holds the GIL throughout.

## Why the worker exists (must not be lost)

`_run_with_timeout`'s docstring names three properties, all load-bearing:

1. the caller is freed after `timeout` even if the regex is still running;
2. a timed-out call returns `None`, treated as no-match (fail-open);
3. **a later regex still runs normally after one has hung.**

(3) is the trap: a hung job blocks its worker forever, so naive reuse would queue every
later regex behind it and time them all out — one bad pattern becoming a total detection
outage. The current code retires the worker on timeout to preserve it.

## Requirements

**R1 — Identical verdicts.** For every input, the set of matched rule ids, the winning
action, redaction hints, rewrite hints and the message must be byte-identical to the
current implementation. This is a pure cost change; it must not alter *what* is matched.

**R2 — Keep all three timeout properties.** The caller must still be freed under a
wall-clock budget; a timeout must still fail open; and one hung pattern must not suppress
evaluation of the others.

**R3 — Bounded caller block.** The time a caller can block must not scale with rule count.
N rules must not be able to block the caller for N × budget.

**R4 — Measured reduction.** The policy stage must fall materially in the E2E trace, not
only in the microbenchmark. The prediction under test: it falls by **more** than the 53%
the idle bench predicts, because contention amplifies handoff cost. A fall of ~53% and no
further would refute the GIL-ping-pong explanation and must be reported as such.

**R5 — No new failure mode under load.** Retirement-and-replacement behaviour must still
self-heal, and a batch must not be able to leak a late result into a later request's answer.

**R6 — Keyword rules untouched.** They never used the worker (`_evaluate_rule`'s keywords
branch returns directly) and cost 1.20 µs/rule. They stay as they are.

## Explicit non-goals

- **Not** hoisting the repeated `text.lower()`. Measured saving: **4.3 µs**. It reads like
  an obvious win and is not one.
- **Not** a literal/Hyperscan prefilter. 65 of 92 rules are skippable on a benign prompt,
  which is a larger prize, but it changes *which* regexes run and so needs its own
  equivalence gate. Separate task.
- **Not** relaxing `_has_redos_shape` or the compile-time rejections.
