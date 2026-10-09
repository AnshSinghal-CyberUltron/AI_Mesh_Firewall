# Requirements Document

## Introduction

This feature implements correction-register item **R2-06 (CRITICAL)**, delivered by card **GW12b ("Bounded holdback")** of the v3 backend-rewrite runbook. The target is the active rewrite tree at `gateway_v2/gateway_v2/`.

**The defect.** The owner-signed streaming-output holdback limit of "no more than 3 upstream tokens held" is not implemented in any measured build. The streaming output-redaction path holds back any trailing suffix that could still grow into a sensitive match until the next upstream token arrives. Measured failures: a UUID was held for 36 upstream tokens, a sha256 for 34, a URL for 15; base64 blobs were held whole; and the per-chunk rescan loop blocked for 17 ms per chunk at a 16 KB chunk size. The prior "worst-chunk latency" metric (C4) hid these holds by construction because a byte waiting on upstream disambiguation was never attributed to gateway compute.

**The fix.** Implement and prove the owner-signed holdback bound inside the streaming egress path. The holdback mechanism holds the minimal suffix that could still become a match, bounds how long that suffix may be held, bounds per-chunk rescan work to a window, publishes a held-tokens histogram, and carries an explicit, documented, testable trade-off for sensitive patterns that are longer than the hold cap. The bound engages only when the pinned plan has an enforcing OUTPUT rule.

**Owner locks already decided (not re-decided here).** `gateway_v2/gateway_v2/domain/locks.py` defines `RELAXED_HOLDBACK_CLASSES = {uuid, sha256, url, base64}` — the classes permitted to exceed the 3-token cap and receive the documented trade-off treatment. In-flight kill behavior is `InFlightKill.CUT_NEXT_CHUNK`.

**Local-verification constraint.** The user cannot run cloud or fleet-scale load tests. Every bound in this document is scoped to be verifiable locally with unit tests, property-based tests, and a local replay benchmark. The full 200 RPS fleet capacity run (L12b-2) and the GW20b fleet certification are **deferred cloud gates**; this feature provides a local, scaled-down concurrency/isolation equivalent and explicitly records that the full cloud certification is out of local scope.

**Reference, not target.** A working holdback prototype exists at `docs/plans/evidence/2026-09-23-runbook-v2-validation/rvproto/rvproto/detect/holdback.py` and `.../rvproto/egress/stream.py` (`hold_start`, `HoldbackOverflow`, separate `holdback_wait_ns`/`release_lag_ns` metrics). It informs the approach only. The production targets `gateway_v2/gateway_v2/egress/stream.py`, `gateway_v2/gateway_v2/egress/output_guard.py`, and `gateway_v2/gateway_v2/detect/windowing.py` are currently stubs.

## Glossary

- **Holdback_Scanner**: The component that, given the pending unreleased buffer of a text stream, computes the earliest byte index whose suffix could still grow into an output-pattern match. Bytes before that index are safe to scan and release.
- **Stream_Pipeline**: The streaming egress component (`gateway_v2/gateway_v2/egress/stream.py`) that parses upstream chunks, drives the Holdback_Scanner per text stream, applies the output Decision to released bytes, and re-serializes downstream frames.
- **Output_Guard**: The output detect → resolve → emit component (`gateway_v2/gateway_v2/egress/output_guard.py`) that produces the output Decision applied to released bytes.
- **Windowing**: The segmentation/window-bounding component (`gateway_v2/gateway_v2/detect/windowing.py`) that bounds pattern lengths and tail-rescan extent so per-chunk work is proportional to the window, not the stream.
- **Upstream_Token**: A unit of upstream output as counted by the holdback contract. The hold cap and histogram are expressed in Upstream_Tokens.
- **Hold_Cap**: The hard maximum number of Upstream_Tokens that may be held for a word-class pattern, read from config `RV_HOLDBACK_MAX_TOKENS` (default 3).
- **Window**: The bounded span of trailing buffer the Holdback_Scanner and tail rescan may inspect per chunk, derived from the longest bounded pattern length.
- **Word_Class_Pattern**: A sensitive pattern whose class is NOT in `RELAXED_HOLDBACK_CLASSES` (for example AWS keys, API tokens, JWTs, emails, card numbers). These are subject to the Hold_Cap.
- **Relaxed_Class_Pattern**: A sensitive pattern whose class is in `RELAXED_HOLDBACK_CLASSES = {uuid, sha256, url, base64}`. These may exceed the Hold_Cap and receive the documented trade-off treatment.
- **Released_Bytes**: Bytes of a text stream that the Stream_Pipeline has written downstream (after any redaction).
- **Redacted_Span**: A contiguous range of input bytes replaced by a redaction placeholder before release.
- **Trade_Off_Outcome**: The declared, explicit behavior when a sensitive match completes on text that was already released because the Hold_Cap forced release. For this feature the declared outcome is **(a) redact the remainder of the match** by default, with the stream-terminate alternative available via the detector trade-off policy per class.
- **Terminal_Error_Frame**: The downstream error frame that terminates the stream (for example on holdback overflow or an output block).
- **T_release_processing**: Gateway compute time between an upstream chunk arriving and its safe downstream write. Governed by the p99 < 20 ms SLO.
- **T_holdback_wait**: Time a released byte waited for disambiguating upstream bytes. Published SEPARATELY from T_release_processing with its own signed bound.
- **Held_Tokens_Histogram**: The observability histogram of held Upstream_Tokens per stream.
- **Enforcing_Output_Rule**: An OUTPUT rule in the pinned plan whose disposition redacts or blocks (that is, enforces rather than only flags/allows).
- **Replay_Benchmark**: The local test (L12b-1) that replays fixed content classes at fixed chunk sizes and asserts the hold and per-chunk-latency bounds.
- **Local_Concurrency_Test**: The local, scaled-down equivalent of the deferred fleet capacity run (L12b-2) that asserts a heavy base64/UUID stream does not degrade a co-running stream's per-chunk latency.

## Requirements

### Requirement 1: Configurable hard hold cap in upstream tokens

**User Story:** As the platform owner, I want a hard cap on how many upstream tokens may be held during streaming redaction, so that the signed "no more than 3 tokens held" bound is enforced rather than aspirational.

#### Acceptance Criteria

1. THE Stream_Pipeline SHALL read the Hold_Cap from configuration key `RV_HOLDBACK_MAX_TOKENS` as a positive integer in the range 1 to 100 Upstream_Tokens inclusive.
2. WHERE `RV_HOLDBACK_MAX_TOKENS` is not set, THE Stream_Pipeline SHALL use a default Hold_Cap of 3 Upstream_Tokens.
3. WHILE a Word_Class_Pattern is the only reason the pending buffer is held, THE Stream_Pipeline SHALL hold no more than Hold_Cap Upstream_Tokens before releasing.
4. IF holding a Word_Class_Pattern suffix would require holding more than Hold_Cap Upstream_Tokens, THEN THE Stream_Pipeline SHALL release the oldest held Upstream_Tokens until the count of held Upstream_Tokens is less than or equal to Hold_Cap.
5. WHERE the held suffix is a Relaxed_Class_Pattern (class in `RELAXED_HOLDBACK_CLASSES`), THE Stream_Pipeline SHALL permit the hold to exceed Hold_Cap by no more than Hold_Cap additional Upstream_Tokens and SHALL apply the Trade_Off_Outcome.
6. IF `RV_HOLDBACK_MAX_TOKENS` is set to a value that is non-integer, less than 1, or greater than 100, THEN THE Stream_Pipeline SHALL use the default Hold_Cap of 3 Upstream_Tokens and SHALL emit a configuration warning indicating the value was rejected.

### Requirement 2: Holdback engages only under an enforcing output rule

**User Story:** As a tenant, I want the holdback mechanism to add latency only when my pinned plan actually enforces an output rule, so that streams with no output enforcement are not delayed.

#### Acceptance Criteria

1. WHERE the pinned plan contains at least one Enforcing_Output_Rule, THE Stream_Pipeline SHALL apply the Hold_Cap and the byte ceiling for unbroken runs to the output stream before releasing held bytes downstream.
2. WHERE the pinned plan contains no Enforcing_Output_Rule, THE Stream_Pipeline SHALL release each upstream chunk downstream within 10 milliseconds of receipt without holding any bytes for disambiguation.
3. WHEN the pinned plan is resolved and before the first upstream chunk is processed, THE Stream_Pipeline SHALL determine and record whether at least one Enforcing_Output_Rule is present.
4. IF the pinned plan cannot be resolved or the Enforcing_Output_Rule presence cannot be determined before the first upstream chunk is processed, THEN THE Stream_Pipeline SHALL withhold the stream and return an error indication to the caller signaling that output enforcement state is undetermined, having released no bytes downstream.

### Requirement 3: Minimal pattern-aware holdback

**User Story:** As a security engineer, I want only the minimal suffix that could still become a match to be held, so that no sensitive value is released in incomplete, unredacted form while non-matching bytes flow through promptly.

#### Acceptance Criteria

1. WHEN the pending buffer is scanned, THE Holdback_Scanner SHALL return the earliest byte index at or after which the remaining suffix could still grow into an output-pattern match, or the buffer length when no such suffix exists.
2. WHEN the Holdback_Scanner returns an index, THE Stream_Pipeline SHALL retain the bytes at and after that index as held bytes and SHALL release all bytes before that index within 50 milliseconds of scan completion.
3. WHEN a completed sensitive match ends within the bytes being released, THE Stream_Pipeline SHALL apply the output Decision's redaction to that match as a Redacted_Span before releasing any byte of that match.
4. IF the output Decision for a completed match within the bytes being released is BLOCK, THEN THE Stream_Pipeline SHALL withhold all unreleased bytes, emit a Terminal_Error_Frame indicating a blocked output, and stop the stream.
5. WHEN an upstream chunk carries the final token of a text stream, THE Stream_Pipeline SHALL apply the output Decision to all remaining held bytes and release every remaining held byte for that stream.
6. IF the Holdback_Scanner fails to complete a scan of the pending buffer, THEN THE Stream_Pipeline SHALL retain all pending bytes as held bytes, emit a Terminal_Error_Frame indicating a scan failure, and stop the stream without releasing any held byte.

### Requirement 4: Bounded window and per-chunk latency

**User Story:** As a performance engineer, I want per-chunk rescan work bounded by a fixed window rather than the stream length, so that per-chunk compute stays flat as the stream grows.

#### Acceptance Criteria

1. THE Windowing component SHALL bound every output pattern length to a configured maximum between 1 and 65536 bytes inclusive.
2. WHEN the Holdback_Scanner rescans the buffer tail, THE Holdback_Scanner SHALL inspect at most Window bytes of trailing buffer, where Window equals the configured maximum pattern length.
3. IF the trailing buffer contains fewer than Window bytes, THEN THE Holdback_Scanner SHALL inspect all available trailing bytes and SHALL NOT block waiting for additional bytes.
4. THE Stream_Pipeline SHALL perform per-chunk holdback work proportional to the Window size and independent of the total number of bytes already released, such that per-chunk holdback processing time does not increase by more than 10% between a stream that has released 1024 bytes and one that has released 10485760 bytes at the same Window size and chunk size.
5. WHEN the Replay_Benchmark runs any specified content class at any specified chunk size between 1 and 65536 bytes inclusive, THE Stream_Pipeline SHALL complete the per-chunk holdback loop in no more than 0.5 ms per chunk measured at the 99th percentile.
6. THE Stream_Pipeline SHALL record T_release_processing such that the 99th percentile value is less than 20 ms.
7. THE Stream_Pipeline SHALL publish T_holdback_wait as a measurement separate from T_release_processing.
8. IF the per-chunk holdback loop exceeds 0.5 ms for any chunk during a Replay_Benchmark run, THEN THE Replay_Benchmark SHALL record the run as failed and SHALL report the measured per-chunk latency that triggered the failure.

### Requirement 5: Held-tokens observability

**User Story:** As an operator, I want a per-stream histogram of held upstream tokens, so that I can confirm the bound holds in production and detect regressions.

#### Acceptance Criteria

1. WHEN a text stream completes, THE Stream_Pipeline SHALL record the maximum number of held Upstream_Tokens observed during that stream as a single integer sample in the Held_Tokens_Histogram.
2. IF a text stream terminates abnormally before completion, THEN THE Stream_Pipeline SHALL record the maximum held Upstream_Tokens observed up to the point of termination into the Held_Tokens_Histogram and SHALL flag the sample as originating from an incomplete stream.
3. WHEN a text stream completes, THE Stream_Pipeline SHALL record T_holdback_wait and T_release_processing as two separate measurements, each expressed in milliseconds.
4. WHERE a Word_Class_Pattern stream completes, THE Held_Tokens_Histogram SHALL report a p50 no greater than 2 Upstream_Tokens and a p99 no greater than 3 Upstream_Tokens, computed over a sample window of at least 1,000 completed streams.
5. WHEN a text stream completes, THE Stream_Pipeline SHALL publish the maximum byte count held for the longest unbroken run during that stream alongside the held-token bounds, expressed as a non-negative integer number of bytes.

### Requirement 6: Documented detector trade-off for over-cap patterns

**User Story:** As a compliance owner, I want an explicit, documented, testable outcome for sensitive patterns longer than the hold cap, so that forced release never silently leaks a credential or PII value.

#### Acceptance Criteria

1. THE feature SHALL document, for each credential and PII pattern that can exceed the Hold_Cap, exactly one declared Trade_Off_Outcome drawn from the set {redact-the-remainder, terminate-the-stream}, such that no documented pattern has zero or more than one declared Trade_Off_Outcome.
2. WHEN a sensitive match completes on text already released because the Hold_Cap forced release, THE Stream_Pipeline SHALL apply the pattern's declared Trade_Off_Outcome within 50 milliseconds of match completion.
3. WHERE the declared Trade_Off_Outcome is redact-the-remainder, WHEN a sensitive match completes on partially released text, THE Stream_Pipeline SHALL replace every byte of the still-held remainder of the match with a redaction placeholder before releasing any further bytes of that remainder.
4. WHERE the declared Trade_Off_Outcome is terminate-the-stream, WHEN a sensitive match completes on partially released text, THE Stream_Pipeline SHALL emit a Terminal_Error_Frame indicating a forced-release trade-off termination and SHALL emit no further content frames for that stream.
5. IF the held buffer size reaches or exceeds the contract byte ceiling for a Relaxed_Class_Pattern, THEN THE Stream_Pipeline SHALL emit a Terminal_Error_Frame indicating holdback overflow, SHALL release no held bytes of the in-progress match, and SHALL emit no further content frames for that stream.
6. IF a credential or PII pattern that can exceed the Hold_Cap has no declared Trade_Off_Outcome at evaluation time, THEN THE Stream_Pipeline SHALL emit a Terminal_Error_Frame indicating an undefined trade-off and SHALL emit no further content frames for that stream.

### Requirement 7: In-flight kill behavior

**User Story:** As the platform owner, I want a killed org's in-flight stream to stop at a defined boundary, so that kill semantics are deterministic.

#### Acceptance Criteria

1. WHILE a stream is in flight and the owning org is killed, THE Stream_Pipeline SHALL stop the stream per `InFlightKill.CUT_NEXT_CHUNK` by ceasing emission no later than the next upstream chunk boundary, where a chunk boundary is the delivery of one complete upstream chunk.
2. WHEN the owning org is killed during an in-flight stream, THE Stream_Pipeline SHALL emit zero additional upstream chunks after the chunk boundary at which the cut takes effect.
3. WHEN the stream is cut per `InFlightKill.CUT_NEXT_CHUNK`, THE Stream_Pipeline SHALL discard all held bytes that have not already passed the output Decision and SHALL NOT release them to the caller.
4. WHEN the stream is cut per `InFlightKill.CUT_NEXT_CHUNK`, THE Stream_Pipeline SHALL terminate the stream within 1 second of the kill signal being observed and SHALL indicate to the caller that the stream was terminated by a kill action.
5. IF the kill signal is observed after the final upstream chunk has already passed the output Decision and been released, THEN THE Stream_Pipeline SHALL complete the stream normally and SHALL NOT retract any already-released bytes.

### Requirement 8: Local replay benchmark (L12b-1)

**User Story:** As a developer, I want a local replay benchmark across representative content classes and chunk sizes, so that the hold and latency bounds are provable without cloud resources.

#### Acceptance Criteria

1. THE Replay_Benchmark SHALL replay each of the 8 content classes (prose, URL, UUID, sha256, paths, JSON, code, and base64) at each of the 3 chunk sizes (2 KB, 8 KB, and 16 KB), producing results for all 24 class-by-chunk-size combinations.
2. WHEN the Replay_Benchmark completes for a Word_Class_Pattern class at a given chunk size, THE Replay_Benchmark SHALL assert the maximum observed hold is no greater than Hold_Cap Upstream_Tokens.
3. IF the maximum observed hold for a Word_Class_Pattern class at any chunk size exceeds Hold_Cap Upstream_Tokens, THEN THE Replay_Benchmark SHALL fail the run and report the offending content class, chunk size, and observed hold value.
4. WHEN the Replay_Benchmark completes for any content class at any chunk size, THE Replay_Benchmark SHALL assert the per-chunk holdback loop block is no greater than 0.5 ms, measured as the maximum per-chunk block across all chunks in that run.
5. IF the per-chunk holdback loop block for any content class at any chunk size exceeds 0.5 ms, THEN THE Replay_Benchmark SHALL fail the run and report the offending content class, chunk size, and observed per-chunk block value.
6. THE Replay_Benchmark SHALL run to completion using only local resources, with no dependency on cloud or fleet infrastructure, and SHALL report an aggregate pass result only when all 24 class-by-chunk-size combinations satisfy both the Hold_Cap and 0.5 ms per-chunk bounds.

### Requirement 9: Local detector regression (L12b-3)

**User Story:** As a security engineer, I want real sensitive patterns split across chunk boundaries to produce their declared outcome, so that holdback cannot be defeated by chunk alignment.

#### Acceptance Criteria

1. WHEN a sensitive pattern is split across two or more upstream chunks at any byte offset within the pattern, THE Stream_Pipeline SHALL produce the same Trade_Off_Outcome that the pattern produces when delivered within a single chunk.
2. IF a sensitive pattern is split such that no individual chunk contains a complete match, THEN THE Stream_Pipeline SHALL buffer unemitted bytes until the pattern is fully assembled or the stream terminates, and SHALL produce the pattern's declared Trade_Off_Outcome rather than emitting the pattern's bytes to the caller.
3. THE detector regression test SHALL cover AWS keys, API tokens, JWTs, email addresses, and payment card numbers, with each pattern split across chunk boundaries at a minimum of three distinct split offsets (first byte, a middle byte, and last byte of the pattern).
4. WHEN the detector regression test executes, THE Stream_Pipeline SHALL produce byte-identical Trade_Off_Outcome results across every tested split offset for a given pattern.
5. THE detector regression test SHALL run to completion on a single local host within 300 seconds and SHALL NOT require any cloud, fleet, or multi-zone infrastructure.

### Requirement 10: Local concurrency/isolation equivalent and deferred cloud gates (L12b-2, GW20b)

**User Story:** As a developer without cloud access, I want a scaled-down local proof that a heavy stream does not degrade a co-running stream, with the full fleet certification clearly marked deferred, so that local work is complete and the remaining cloud gate is explicit.

#### Acceptance Criteria

1. WHEN a heavy base64-16KB or UUID-heavy stream runs concurrently with exactly one co-running stream on the local host, THE Local_Concurrency_Test SHALL assert that the co-running stream's per-chunk latency, measured as the median across all chunks, does not increase by more than 0.5 ms relative to its baseline per-chunk latency measured with no concurrent heavy stream.
2. WHILE no cloud or fleet infrastructure is reachable, THE Local_Concurrency_Test SHALL execute to completion using only local-host resources and SHALL NOT require any network connection to external fleet or cloud services.
3. IF the co-running stream's measured median per-chunk latency increase exceeds 0.5 ms, THEN THE Local_Concurrency_Test SHALL fail and SHALL report a result indicating the measured latency degradation and the 0.5 ms threshold that was exceeded.
4. IF the heavy stream or the co-running stream terminates abnormally before producing at least one measurable chunk, THEN THE Local_Concurrency_Test SHALL fail and SHALL report a result indicating that the latency assertion could not be evaluated.
5. THE feature documentation SHALL record that the 200 RPS fleet capacity run (L12b-2) and the GW20b fleet certification are deferred cloud gates that are out of local scope, and SHALL identify each by its gate identifier (L12b-2 and GW20b).

## Correctness Properties (for property-based testing)

These properties express invariants the implementation must preserve and are intended for property-based tests over generated streams, chunk boundaries, and content classes.

1. **No unredacted sensitive release (safety invariant).** FOR ALL generated streams and chunk splits, no completed sensitive match is ever present in Released_Bytes in unredacted form. (Invariant)
2. **Word-class hold cap.** FOR ALL streams whose only held pattern is a Word_Class_Pattern, the held Upstream_Token count never exceeds Hold_Cap. (Invariant)
3. **Window-bounded work.** FOR ALL chunks, the number of buffer bytes the Holdback_Scanner inspects is no greater than Window, independent of total released bytes. (Metamorphic: per-chunk work does not grow with stream length)
4. **Released equals input minus redacted.** FOR ALL streams, the concatenation of Released_Bytes equals the concatenation of input text bytes with every Redacted_Span replaced by its placeholder — no extra bytes added, none dropped outside redaction. (Round-trip / reconstruction property)
5. **Chunk-split invariance.** FOR ALL input texts, the set of completed matches and their declared outcomes is independent of how the text is split into upstream chunks. (Metamorphic / confluence)
6. **Trade-off completeness.** FOR ALL over-cap sensitive matches, every completed match produces exactly its declared Trade_Off_Outcome (redact-remainder or terminate). (Error-condition completeness)
7. **Idempotent re-scan.** Re-scanning an already-released, already-redacted buffer prefix produces no additional release or redaction. (Idempotence: f(x) = f(f(x)))
8. **Separate latency accounting.** FOR ALL released pieces, T_release_lag equals T_release_processing plus T_holdback_wait, and the two are accounted separately. (Invariant on the latency decomposition)

## Testing Notes

- **In local scope:** Holdback_Scanner logic, Hold_Cap enforcement, O(window) bound, per-chunk latency bound, Held_Tokens_Histogram, Trade_Off_Outcome behavior, L12b-1 replay benchmark, L12b-3 detector regression, and the L12b-2 local concurrency/isolation equivalent — all as unit tests, property-based tests, and a local replay benchmark.
- **Deferred cloud gates (out of local scope):** the full 200 RPS fleet capacity run (L12b-2) and the GW20b fleet certification. These require cloud/fleet infrastructure and are represented locally only by the scaled-down concurrency/isolation test in Requirement 10.
