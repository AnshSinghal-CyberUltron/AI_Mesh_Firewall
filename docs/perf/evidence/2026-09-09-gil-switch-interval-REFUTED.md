# REFUTED — GIL switch interval is not the p99 stall

## The hypothesis

CPython's GIL switch interval is 5 ms. A thread wanting the GIL waits a full interval before
the holder is even *asked* to yield. The chat path crosses thread boundaries repeatedly (11
`asyncio.to_thread` sites, the scanner's executor, the policy regex worker), each crossing
being two acquisitions. Three or four 5 ms waits ≈ 15–20 ms — the size of the observed stall.

**Prediction:** dropping the interval to 0.5 ms shrinks the stall ~10×.

## Result — four arms, `--repeat 3`, stub pinned at 0.5 s, concurrency 16

| interval | p50 | p90 (spread) | p99 (spread) | RPS | gw CPU |
|---|---|---|---|---|---|
| **5 ms (default)** | 17.00 | 33.5 (33.3–36.6) | 71.50 (58.0–105.5) | 28.6 | 105.6% |
| 1 ms | 18.30 | 39.6 (34.6–39.6) | 56.10 (47.5–59.5) | 28.3 | 111.5% |
| 0.5 ms | 17.80 | 42.3 (35.3–42.3) | 67.50 (52.2–75.1) | 28.3 | 106.2% |
| 0.1 ms | 17.10 | 38.0 (38.0–40.6) | 63.50 (56.5–78.1) | 28.6 | 101.5% |

**No 10× anything.** Every p99 spread overlaps the default's. p50 is flat within ±1.3 ms.

And the cost side that R3 required be measured shows the change is actively mildly harmful:

- **p90 degrades monotonically** as the interval drops: 33.5 → 39.6 → 42.3 (0.1 ms recovers
  to 38.0, still worse than default).
- **`overhead_ms` tail grows 1.5 → 3.9 → 4.7 → 8.6 ms** — time outside every stage, rising
  exactly as switching gets more frequent. That is the context-switch cost arriving, with no
  latency win to pay for it.

## What this rules out

GIL-acquisition latency is not the mechanism. That was the largest remaining plausible
candidate, and it is now gone alongside host noise (steal = 0, load 0.49/16), CPU saturation
(103% of 400%), GC (`gc_pause_ms` 0.00), CFS throttling (refuted earlier) and worker count
(12/16/24 all inside the same band).

The knob is left in place, env-gated and **unset by default**, because it costs nothing when
empty and the measurement is worth being able to repeat. It is not a recommended setting.

## Where to look next

The one candidate the data actively points at: at 0.1 ms, `policy` became 46% of outliers
and `overhead_ms`'s tail grew to 8.6 ms — the stall moves toward *scheduling* as switching
increases, not toward any stage's work.

The decisive next measurement is whether the stall exists **independent of the firewall at
all**: drive a trivial endpoint concurrently with the chat load. If a no-op request also
shows a ~50 ms p99, the event loop is the constraint and no amount of stage optimisation can
reach the target. If the no-op stays flat, the stall is genuinely inside the chat path and
the search continues there.

That test distinguishes "the firewall is slow" from "the process cannot schedule promptly",
which every measurement so far has been unable to separate.
