# Design — one env-gated call, measured with repeats

## Change

At gateway startup, alongside the existing `gc_monitor.init()` and `_build_prefilter()`
registrations:

```python
_switch = os.environ.get("GATEWAY_GIL_SWITCH_INTERVAL_S", "").strip()
if _switch:
    sys.setswitchinterval(float(_switch))
```

Absent or empty → CPython's 5 ms default, i.e. today's behaviour exactly. That satisfies R4
and makes the rollback a config change rather than a deploy.

## Why this is the right probe

The mechanism is specific and checkable: a thread waiting for the GIL sets the drop-request
flag only **after** `switchinterval` elapses. Until then it simply waits, even when the
holder would have released at its next bytecode boundary anyway.

Regex matching makes this concrete. A single `re.search` over 4096 chars is ~75 µs of C
code holding the GIL, and the policy batch runs 46 of them in a loop. Between patterns the
interpreter returns to bytecode, so the GIL *could* change hands every ~75 µs — but a
waiter still burns a full 5 ms before asking. The holder is not the problem; the asking
delay is.

## What refutes it

If p99 stays in the 56–107 band at 0.5 ms and 0.1 ms, GIL-acquisition latency is not the
stall, and the next candidates are the event loop being blocked by a single long
synchronous section, or Redis round-trip spikes. Recorded either way — a refutation here is
as useful as a confirmation, because it removes the largest remaining plausible mechanism.

## Measurement

Four arms — default (5 ms), 1 ms, 0.5 ms, 0.1 ms — each `--repeat 3` at concurrency 16,
stub pinned at 0.5 s by R7. Report p50/p90/p99 **with spread**, plus RPS and gateway CPU so
R3's cost side is visible. The arms differ by one environment variable and nothing else.
