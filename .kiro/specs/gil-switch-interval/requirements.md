# Requirements — find the source of the p99 stall

## The problem

At a fixed configuration, p99 addon lands anywhere in **56–107 ms** while p50 is stable
to ±1 ms. A p99 target cannot be demonstrated against a 2× band, so this blocks the
< 20 ms claim entirely. (`2026-09-09-variance-is-the-obstacle.md`)

Per-request attribution shows the excess landing on a **different stage each run** —
rate_limit, auth, model_routing, output_guardrail, kill_switch all take turns — each
reporting a similar **13–25 ms** mean excess when it is the outlier. A slow stage produces
excess in *that* stage. This is a stall charged to whichever stage was open when it hit.

## Ruled out already

| candidate | evidence |
|---|---|
| host neighbours | load 0.49 of 16 CPUs, **steal = 0** |
| CPU saturation | gateway uses 103.8% of 400% available |
| GC | `gc_pause_ms` = 0.00 |
| CFS throttling | refuted earlier in this project |
| worker count | 12/16/24 workers all sit in the same 56–107 band |

## The hypothesis under test

Python's GIL switch interval is **5 ms** (`sys.getswitchinterval()` confirmed 0.005 in the
running container). A thread that wants the GIL while another holds it waits up to one
switch interval before the holder is even *asked* to yield.

The chat path crosses thread boundaries repeatedly: 11 `asyncio.to_thread` sites in
`main.py`, the scanner's `run_in_executor`, and the policy engine's regex worker. Each
crossing is two GIL acquisitions (into the thread, back to the loop). Under concurrency,
several of those can each wait ~5 ms.

**Three or four such waits is 15–20 ms — the observed excess.**

## Requirements

**R1 — Falsifiable.** Lowering the switch interval to 0.5 ms must reduce the stall roughly
10× if the mechanism is GIL-acquisition latency. If p99 does not move, the hypothesis is
**refuted** and must be recorded as such.

**R2 — Measured with repeats.** The variance being investigated is exactly what makes n=1
useless. Every arm runs `--repeat 3`. This project has already produced two conclusions
from n=1 arms that did not survive repetition; that must not happen a third time.

**R3 — Cost side measured too.** A shorter interval means more context switches. Gateway
CPU and RPS must be reported alongside latency, not just the latency win.

**R4 — Off by default, env-gated.** The setting must not change behaviour unless explicitly
enabled, so it can ship dark and be reverted by configuration alone.

**R5 — No verdict change.** This touches scheduling only. No detection, policy or
enforcement behaviour may differ.

## Non-goals

- Not removing thread hops. The reviewed plan **withdraws** policy/input_scan parallelism
  ("prefetch independent state, but preserve the existing gate order and short-circuits"),
  and the thread hops are what keep the event loop unblocked.
- Not raising worker count — already tested, already in the same band.
