# The firewall is CPU-bound: 13.25 ms/request, ~75 RPS per vCPU

## The measurement

Scan-only probe (`max_tokens: 0`, no model call), 4096-char prompt, unique per request,
policies active, concurrency 1. CPU read from `/proc/*/stat` (utime+stime) across every
process in the gateway container, over 40 requests:

```
wall per request    13.47 ms
CPU  per request    13.25 ms
→ 98.4% of the firewall's cost IS CPU
```

**This corrects an inference I had drawn twice.** `gw_cpu%` reads 5–9% at low concurrency,
and I twice took that as "the process is idle, so the latency is waiting". It is not: a low
percentage at 1.9 RPS is simply a low request *rate*. Per request, the work is CPU almost
end to end.

## What that gives us — the first honest throughput number

**~75 RPS per vCPU** (1000 / 13.25), ≈ **302 RPS** on this 4-vCPU container.

This is a *measured* firewall cost, not a stub-bound harness artefact. The load harness has
been refusing to report RPS/vCPU throughout, correctly: *"gateway used 0.05 of its 4.0 vCPU
limit at the knee. Something other than the gateway binds."* The 0.5 s stub sets RPS to
`concurrency / 0.5`, so no run through it could ever have measured throughput.

For scale: **100k RPS would need ~1,330 vCPUs** at this cost per request. The plan documents'
own operating point is ~1,064 RPS, which is ~14 vCPUs of firewall — consistent with this
measurement, and nowhere near 100k.

## Where the 13.25 ms goes (py-spy, same configuration)

```
39.6%  redact_all              <- redacting text for the operator TRACE
26.0%  _scan_all               <- the policy engine: the actual security work
20.2%  _trace_text -> _redact_trace_text
19.4%  build_pipeline_trace -> _truncate
10.2%  _redact_obfuscated
```

**The firewall spends more CPU redacting text for the trace than it spends evaluating
policy.** ~2.7 ms/request on a diagnostic artefact against ~3.4 ms on detection.

`_truncate` (`pipeline_trace.py:767`) redacts the **full** text and then keeps 1200 chars.
The comment gives a real reason — redacting after truncation would leave a secret straddling
the cut-point partly visible — but that needs a bounded overlap, not the whole prompt. Cost
is linear at ~1.2 µs/char, so trace redaction is currently **unbounded in prompt length**: an
8 KB prompt already costs 9.75 ms, a 32 KB prompt would cost ~39 ms, purely for diagnostics.

## Hyperscan: the prefilter had never once run

`patterns._build_prefilter()` (task 7) compiles 56 patterns into a Hyperscan database and is
written to no-op when the wheel is absent. **The wheel was absent**, so the prefilter never
ran — and `scripts/detection/hyperscan_prefilter_gate.py`, which exists to prove it never
under-reports, had only ever tested the fallback.

Now declared in `gateway/pyproject.toml` and locked. With it present:

| | without | with | |
|---|---|---|---|
| `redact_all`, unique 4096-char input | 4.88 ms | **2.67 ms** | 1.83× |

**1.83×, not the 7.4× claimed in `_build_prefilter`'s docstring.** The prefilter accelerates
pattern matching; it does not touch `_redact_obfuscated` or `_iter_transport_decodes`, which
the profile shows are a further ~14% and ~9%.

Safety, now that the path actually executes:
- prefilter gate: **762 inputs, 0 under-reports**, 56 patterns compiled, 7 with zero-width
  assertions correctly rejected and left on `re`;
- test suite A/B, hyperscan active vs blocked: **74 failed / 812 passed in both arms** —
  byte-identical outcomes, zero failures introduced.

End-to-end p50 moved only ~0.3 ms, because trace redaction is *one* cache-missing call per
request rather than the several the first profile implied.

## A startup log that lied

```python
from patterns import _build_prefilter, _PREFILTER_KEYS   # bound by VALUE, pre-build
if _build_prefilter() is not None:
    LOG.info("Multi-pattern prefilter ready (%d patterns)", len(_PREFILTER_KEYS))
```

`_PREFILTER_KEYS` is bound at import, before `_build_prefilter()` populates it, so the line
printed **"ready (0 patterns)"** while 56 were compiled and active. Fixed to read the module
attribute after the build.

That is the third component this session found reporting a healthy-looking status while its
actual state was different — after the policy engine evaluating zero rules, and Tier-1
emitting no verdict. A status line is not evidence of work.
