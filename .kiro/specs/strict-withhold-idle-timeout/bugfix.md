# Bugfix Requirements Document

## Introduction

STRICT_WITHHOLD streams whose total upstream time exceeds `idle_timeout_s` are cut with a false HTTP 408 `stream_idle_timeout` ("The downstream client went idle"), even though the upstream is healthy and the client is connected and reading. In STRICT_WITHHOLD the gateway intentionally writes nothing downstream until the whole response has been inspected and resolved, but the Idle_Timeout (GW12 R9.3) measures the gap since the last completed downstream write, seeded at stream start. The first and only content write therefore trips the idle check whenever the withheld stream took longer than `idle_timeout_s`, and the failure is blamed on the client.

Impact: GW33 D-20 promotes every org with an enforcing IP-leak or exfil rule to STRICT_WITHHOLD, so long real-model answers for those orgs fail before any byte is sent. Source: integration run of 4 Nov 2026, finding F1 (`docs/plans/evidence/2026-11-04-integration/FINDINGS.md`; evidence `l34-python-sdk-strict-withhold.jsonl`, cancellation cell). Observed: a 6 s stream with a chunk every 150 ms returned 408 before any byte.

Owner decision (already made): fix the signal mismatch for strict mode specifically. Do not raise the global `idle_timeout_s`. Strict-mode streams get a liveness signal based on upstream activity, so a genuinely stalled upstream is still killed within a bound and a healthy withheld stream is not.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a STRICT_WITHHOLD stream with an enforcing output rule withholds content for longer than `idle_timeout_s` while the upstream delivers chunks within `inter_chunk_timeout_s` and the client is connected and accepting bytes THEN the system cuts the stream at the first downstream write with `stream_idle_timeout` (HTTP 408), before any content byte is sent.

1.2 WHEN the false idle cut in 1.1 occurs THEN the system reports "The downstream client went idle", blaming the client although the client never stopped reading and no write was ever blocked.

1.3 WHEN a STRICT_WITHHOLD stream is cut by the false idle timeout THEN the system discards a fully received, otherwise allowable (or redactable) response, and the idle-timeout counter and event log count a client fault that did not occur.

### Expected Behavior (Correct)

2.1 WHEN a STRICT_WITHHOLD stream withholds content for longer than `idle_timeout_s` while upstream chunks keep arriving within `inter_chunk_timeout_s` and the client is connected and accepting bytes THEN the system SHALL NOT raise `stream_idle_timeout` and SHALL deliver the decided response (sanitized content, or the terminal `output_blocked` frame) normally.

2.2 WHEN a STRICT_WITHHOLD stream is evaluated for idleness THEN the system SHALL measure liveness from the last upstream activity (arrival of an upstream event of any kind), not from the last downstream write, and SHALL derive any bound it uses from the `ResourceContract` with no new capacity literal outside `runtime/resources.py`.

2.3 WHEN a STRICT_WITHHOLD stream receives no upstream chunk at all for longer than `inter_chunk_timeout_s` THEN the system SHALL still cut the stream within that bound with `stream_inter_chunk_timeout` (the upstream-stall code, not a client-idle code), release resources, and send no content.

2.4 WHEN a STRICT_WITHHOLD stream's final downstream write is accepted slowly or blocks THEN the system SHALL still bound it by `write_timeout_s` and, if the client stops accepting bytes after the decision, SHALL cut with the existing write or idle code, so client back-pressure remains detected once writing begins.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a stream runs in INCREMENTAL mode and the gap since the last completed downstream write exceeds `idle_timeout_s` THEN the system SHALL CONTINUE TO cut it with `stream_idle_timeout` (408) before issuing the write, using the existing write-based model.

3.2 WHEN a stream (any mode) has an upstream that stalls between chunks beyond `inter_chunk_timeout_s` THEN the system SHALL CONTINUE TO cut it with `stream_inter_chunk_timeout`.

3.3 WHEN any write blocks beyond `write_timeout_s` THEN the system SHALL CONTINUE TO cut with `stream_write_timeout`.

3.4 WHEN a stream exceeds `max_stream_duration_s`, or a kill switch, key revocation, plan change or stale snapshot fires THEN the system SHALL CONTINUE TO cut it with the corresponding existing code, and the single `KillLatch` first-cut-wins rule SHALL be unchanged.

3.5 WHEN a routed stream's first upstream event is slow (GW28 D-13/D-18 `first_event_s`) THEN the system SHALL CONTINUE TO apply the wider first-event bound and re-seed the idle clock after the first event.

3.6 WHEN a STRICT_WITHHOLD stream is blocked, redacted, overflows `max_buffer_bytes`, or is killed THEN the system SHALL CONTINUE TO emit exactly the same frames, error codes, zero-content-before-block guarantee, and metrics as today.

3.7 WHEN a STRICT_WITHHOLD plan has no enforcing output rule (pass-through) THEN the system SHALL CONTINUE TO write each chunk as it arrives under the existing write-based idle model.

3.8 WHEN `idle_timeout_s`, `inter_chunk_timeout_s`, `write_timeout_s` or `max_stream_duration_s` are read from the `ResourceContract` THEN the system SHALL CONTINUE TO return the same derived values (the global bounds are not changed).
