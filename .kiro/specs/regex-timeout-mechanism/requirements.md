# Task 2 — the per-regex timeout mechanism

## Problem (measured)

`docs/perf/evidence/2026-09-09-task10-capacity-baseline.md`: the firewall-tax p99 goes
53 → 146 ms between concurrency 4 and 16 while the gateway uses **1.52 of 4 vCPU**. The
median barely moves. That is contention, not saturation.

`policy_engine._run_with_timeout` creates a **fresh daemon thread per regex**, starts it,
then immediately joins it. The caller blocks throughout, so the thread provides no
parallelism. It exists for one reason: Python cannot interrupt a running `re.search`, so
abandoning a daemon thread is the only way to free the caller from a backtracking
pattern.

Measured cost (3,000 iterations, ~1,200-char input):

| approach | per regex | overhead |
|---|---:|---:|
| inline (no guard) | 25.08 µs | — |
| today: thread per regex | 79.70 µs | 54.61 µs |
| persistent worker + queue | 35.37 µs | 10.29 µs |

45 rules: 2.46 → 0.46 ms/request. At 27 RPS the gateway creates ~1,200 threads/second.

## Requirements

**R1 — every safety property preserved, exactly.** The mechanism exists to bound a
runaway regex. Three properties, all of which must survive:

- **P1** the caller is freed after `timeout` even if the regex is still running;
- **P2** a timed-out call returns `None` (treated as "no match" — fail-open, as today);
- **P3** a *subsequent* regex still runs normally after one has hung. Today this holds
  because the stuck thread is abandoned and the next call creates a fresh one. A naive
  persistent worker would break it: the stuck job would block the queue forever and every
  later regex would time out too. **This is the trap in this task.**

**R2 — an exception in `fn` returns `None`**, as today.

**R3 — no cross-thread contention.** The scanner runs in a thread pool; a single shared
worker queue would replace thread-creation contention with lock contention and might not
improve the tail at all.

**R4 — behavioural equivalence proven, not assumed.** Same verdicts over the corpus, plus
direct tests for P1/P2/P3 using a genuinely hanging function.

**R5 — measured against the exact baseline sweep.** Re-run
`load.py --concurrency 4,16,...` on the same configuration. If p99 does not collapse, the
hypothesis in the capacity baseline is **wrong** and must be recorded as wrong rather
than quietly dropped.

## Out of scope

Replacing `re` with a multi-pattern engine (task 7). This task changes only how the
existing engine is invoked.
