# FINDING — `max_stream_duration_s` bounds long strict answers (owner decision)

**Date:** 2026-11-05
**Spec:** `strict-withhold-idle-timeout` (bugfix F1)
**Status:** documentation only — NO code changed, NO `ResourceContract` value changed.

This is a separate finding surfaced while fixing the false `stream_idle_timeout` on
withheld strict streams. It is recorded for an owner decision and is explicitly **out of
scope** for the F1 fix (which touches only the idle-liveness signal). Requirement 3.8 of
the bugfix forbids changing any derived bound, so nothing here is applied.

## The bound

At the default SLO (`_DEFAULT_TARGET_P99_MS = 20.0`, i.e. `p99_s = 0.020 s`) with
`_MAX_STREAM_DURATION_MULT = 15000`:

```
max_stream_duration_s = 15000 × 0.020 s = 300 s
```

`InFlightControl` flips the `KillLatch` when `clock - start > max_stream_duration_s()`,
evaluated at stream start and after every chunk, independently of the idle clock. The F1
liveness re-seed does not touch it, so a withheld strict stream is still bounded in total
wall-clock time at 300 s.

## Why this bites strict answers specifically

A STRICT_WITHHOLD stream with an enforcing output rule writes **nothing** downstream until
the whole response has been inspected and decided. So the wall-clock budget is spent
entirely before any byte reaches the client.

Worked example:

- A reasoning model producing **20,000 output tokens at 50 tokens/s** takes **400 s**.
- 400 s > 300 s, so the stream is cut with `stream_max_duration` **before any byte is
  sent**, and the **entire** decided answer is discarded.

The bound is wall-clock from stream start and is **the same value for INCREMENTAL**. The
difference is in what survives the cut:

- **INCREMENTAL:** the client has already received every byte sent before the cut; only the
  unsent tail is lost.
- **STRICT_WITHHOLD:** nothing was sent, so the cut discards the **whole** answer.

Approximate strict capacity at the 300 s bound:

| Generation rate | Tokens deliverable within 300 s |
|---|---|
| 50 tokens/s | ~15,000 tokens |
| 30 tokens/s | ~9,000 tokens |

A 20,000-token answer exceeds both.

## Options for the owner (not chosen here)

1. **Widen `_MAX_STREAM_DURATION_MULT`** in `runtime/resources.py` globally. Simple, but
   raises the ceiling for every stream in every mode, not just strict.
2. **Add a strict-specific multiplier** in `runtime/resources.py` so STRICT_WITHHOLD
   streams get a larger `max_stream_duration_s` than INCREMENTAL / pass-through. Targets the
   mode that discards the whole answer on a cut, at the cost of a second knob and a longer
   worst-case hold on withheld buffers.
3. **Leave the bound as is.** A 20,000-token strict answer remains capped at ~300 s; orgs
   promoted to strict by GW33 D-20 (enforcing exfil / ip-leak / grounding) with very long
   model answers would need to split the request or accept the cut.

No option is selected. This is an owner decision on whether the 300 s strict ceiling is
acceptable for the promoted-org answer-length distribution.

## Related, unchanged observations

- **Withheld-buffer overflow shrink:** `stream_buffer_bytes(active_streams)` caps the
  withheld buffer, and a strict stream that overflows the cap is cut via the pipeline's
  `_overflow` path (not an idle/duration cut). The per-stream cap **shrinks as
  `active_streams` grows**, so a very long strict answer under high concurrency can hit the
  buffer cap before the 300 s duration bound. Unchanged by F1; noted as a second ceiling on
  withheld streams.
- **Unrouted first-event bound:** on non-routed streams the first upstream event is bounded
  by the plain `inter_chunk_timeout_s` (1 s at the default SLO), because `first_event_s`
  (the wider GW28 D-13/D-18 bound) is only set when routing is on. A slow time-to-first-token
  model on an unrouted org is therefore cut at 1 s with `stream_inter_chunk_timeout`
  (existing behavior, outside the F1 bug and unchanged).
