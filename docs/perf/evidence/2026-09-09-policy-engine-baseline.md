# Policy-engine latency baseline — dev/perf-9stage

**Host:** Intel Xeon Platinum 8581C @ 2.30 GHz (Emerald Rapids), 8 physical / 16 SMT cores
**Tree:** `dev/perf-9stage` @ baseline `a83d113f` · Python 3.14.4
**Scripts:** `scratchpad/aws/policy_bench.py`, `scratchpad/aws/thread_prove.py`
**Tag:** `[M]` — measured this session

## 1. `policy_engine.evaluate`, p50 ms

2:1 regex:keyword mix, prompt-field rules, ≥12 iterations after warmup.

| rules | 512 ch | 2,048 ch | 4,096 ch (~1,024 tok) | RPS/vCPU @4,096 |
|---:|---:|---:|---:|---:|
| 10 | 0.43 | 0.54 | 0.68 | 1,474 |
| 30 | 1.22 | 1.54 | 1.89 | 528 |
| 60 | 2.48 | 3.14 | 3.99 | 251 |
| 120 | 5.02 | 6.37 | 7.95 | 126 |
| 264 | 10.87 | 13.81 | 17.06 | 59 |

**Linear at ~0.065 ms per rule** at the 1,024-token band. Rule count is the dominant latency
variable and is operator configuration, not code.

## 2. The mechanism: `_run_with_timeout` spawns a thread per regex

`policy_engine.py:273-290` does `Thread(...); start(); join(timeout)` — start-then-immediately-join,
so the work is **serial and the thread buys no parallelism**.

**Isolated raw cost: `Thread` create + start + join = 0.0586 ms.** That single figure accounts for
the entire 0.065 ms/rule slope.

## 3. Threaded vs direct `.search()`

| rules | chars | threaded | direct | speedup |
|---:|---:|---:|---:|---:|
| 10 | 512 | 0.599 | 0.051 | 11.8× |
| 60 | 512 | 3.594 | 0.297 | 12.1× |
| 264 | 512 | 15.676 | 1.297 | **12.1×** |
| 10 | 4,096 | 0.969 | 0.400 | 2.4× |
| 60 | 4,096 | 5.765 | 2.407 | 2.4× |
| 264 | 4,096 | 24.930 | 10.432 | **2.4×** |

**The speedup is band-dependent.** At 512 chars the thread is ~92% of the cost (12×). At 4,096 chars
the regex work grows and the thread falls to ~58% (2.4×). A flat multiplier claim is wrong.

## 4. Consequences for the plan

1. Removing the thread is the largest **safe, dependency-free** win: 2.4× at the target band,
   12× at short prompts.
2. It is **not sufficient** at high rule counts — at 264 rules / 4,096 chars the residual
   **10.43 ms is genuine regex scanning**. That is the floor a Python per-pattern loop can reach.
3. Beating that floor requires a single multi-pattern scan (Hyperscan/Vectorscan class); prior
   measurement puts 174 patterns at ~34 µs.
4. A **≤60-rule** package already fits a 20 ms budget today (3.99 ms) and would fall to ~1.7 ms with
   the thread removed. **Rule budget is a same-day lever requiring no code.**

## 5. Supersedes

`docs/plans/2026-08-27-FINAL-evidence-based-hot-path-plan.md` measured `_scan_prompt_sync` at
2,038.9 ms. That method is now a passthrough (`scanner.py:1160`); the figure describes deleted code.
