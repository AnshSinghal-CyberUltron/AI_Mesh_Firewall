# Requirements Document

## Introduction

This feature implements card **GW12** of the AI Mesh Firewall v3 backend rewrite (`gateway_v2/`): the Server-Sent-Events (SSE) egress pipeline with bounded buffers, backpressure, and real cancellation. It is a Protocol-phase card that depends on GW03 (resource contract / capacity) and GW11, and is required by GW13, GW14, GW16b, and GW19.

GW12 stands up the serving skin around the already-shipped bounded-holdback engine (GW12b / R2-06). The holdback engine — the pure scanner (`detect/holdback.py`), the window bound (`detect/windowing.py`), the owner-signed hold cap (`runtime/holdback_config.py`), the label-free latency producer (`runtime/holdback_metrics.py`), and the thin injectable release loop (`egress/stream.py::StreamPipeline` with its `killed()` kill seam and C4 split-latency / C25 byte-linearity properties) — is **DONE**. This spec references those as existing seams to wire into and MUST NOT re-specify or rebuild them.

GW12 builds, in gap-list order: the SSE state machine + codec, the ASGI app assembly (router, lifespan, `/metrics`, `/readyz`, error envelope), the bounded coalescer and credit-based flow control, real client-disconnect cancellation, the no-post-first-byte-splice latch, the dispatch layer (provider client + routing + transform), the C27 inter-chunk/idle/write timeouts, the C24 max-stream-duration and signed in-flight control, mid-stream provider error-frame scanning, the per-request export surface, and the C38 serving-loop discipline.

### Scope boundaries (explicit cross-references)

- **GW12b / R2-06 (bounded holdback) — DONE, OUT OF SCOPE to rebuild.** `detect/holdback.py`, `egress/stream.py::StreamPipeline`, `runtime/holdback_config.py`, `runtime/holdback_metrics.py`, the C4 split-latency series, the C25 byte-linearity + held-byte ceiling, and the `InFlightKill.CUT_NEXT_CHUNK` kill seam are shipped. Requirements here wire into them without changing them.
- **GW13 (output enforcement modes) — SEPARATE DOWNSTREAM CARD, OUT OF SCOPE.** `STRICT_WITHHOLD` and the `INCREMENTAL` mode switch (`egress/strict.py`) belong to GW13. GW12 builds the `INCREMENTAL` streaming SUBSTRATE only; it does not add the mode switch or the whole-response withhold path.
- **GW15–GW18 (endpoint handler bodies) — OUT OF SCOPE.** The chat/responses/misc/mcp/rag handler bodies land in GW15–GW18. GW12 assembles the app + router + lifespan + error envelope and boots with the streaming chat path wired, leaving the other handlers registered-for-later.

### Local-only constraint (standing)

The operator has NO cloud resources. Every requirement MUST be verifiable locally. Three cloud/live gates are recorded as DEFERRED with a local equivalent:
- **LGW12-1** (1,000-stream memory plateau) → N in-process streams over the injected harness asserting the `active_streams × stream_buffer_bytes` bound holds and memory does not grow (pattern: `tests/egress/test_lgw12b_concurrency.py`).
- **LGW12-7** (real-TCP SDK conformance over both OpenAI SDKs) → in-process ASGI transport + recorded-frame conformance (DONE marker, error-frame shape, split-surrogate decode).
- Any 200 RPS / fleet-certification run → stays a cloud gate; the local equivalent is the scaled-down concurrency harness.

## Glossary

- **Gateway**: The `gateway_v2` AI Mesh Firewall serving process that terminates client SSE/JSON requests, forwards to an upstream provider, and enforces output guards.
- **SSE_State_Machine**: The component in `edge/wire/` that serializes an internal `DownstreamFrame` into wire bytes and decodes upstream SSE, producing SDK-parseable frames.
- **DownstreamFrame**: The existing internal value type (`egress/stream.py`) carrying `text_deltas: tuple[tuple[str, str], ...]` and an optional `error_code`. A set `error_code` marks a Terminal_Error_Frame after which no content frame may follow. GW12 serializes it to the wire; it does not redefine it.
- **Terminal_DONE_Marker**: The `data: [DONE]\n\n` SSE frame that signals clean stream termination to an OpenAI SDK client.
- **Error_Frame**: The declared SSE/JSON error shape rendered from a `DownstreamFrame.error_code`, parseable by the official SDKs.
- **ASGI_App**: The real application object assembled in `edge/app.py` (router, lifespan, endpoints), replacing the current `app = None` stub.
- **Error_Envelope**: The single builder in `edge/errors.py` that renders egress/dispatch value-codes into HTTP/SSE responses, honoring the codes-vs-render boundary.
- **ResourceContract**: The existing capacity authority (`runtime/resources.py`) — the ONLY module permitted capacity-position literals (gated by `lint/check_capacity_literals.py`).
- **High_Water_Mark**: The maximum number of buffered bytes a coalescer may hold for one stream before applying backpressure, DERIVED from `ResourceContract.stream_buffer_bytes(active_streams)`. Also called **buffer high-water** when exported per request.
- **Coalescer**: The bounded per-stream buffer that accumulates upstream bytes up to the High_Water_Mark and never grows beyond it.
- **Credit**: A unit of permitted upstream read quota granted to a stream; a stream reads from upstream only while it holds credit, and credit is replenished only as downstream consumes released bytes.
- **Credit_Flow_Control**: The mechanism by which a slow downstream consumer slows upstream reads, bounding total memory to `active_streams × per-stream ceiling`.
- **Active_Streams**: The count of concurrently open streams the Gateway is serving at a given instant, used as the divisor in the per-stream byte-ceiling derivation.
- **First_Byte_Latch**: A per-stream one-way latch that flips the first time a content byte is released downstream; after it flips, no fallback/retry may be spliced into the stream.
- **In_Flight_Kill**: The existing cut seam (`domain/locks.py::InFlightKill.CUT_NEXT_CHUNK` + the `StreamPipeline.killed()` probe) that cuts an in-progress stream at the next chunk boundary. GW12 wires client disconnect, kill switch, key revocation, and plan change into this seam.
- **Client_Disconnect**: The condition where the downstream client closes the connection (observed via the ASGI receive channel) or stops reading past the idle timeout.
- **ProviderClient**: The injectable upstream client protocol (`dispatch/provider.py`) exposing an abortable streaming request the egress loop reads from.
- **Dispatch_Router**: The deterministic, plan-derived upstream selector (`dispatch/routing.py`).
- **Transform**: The byte-verified request/response shaping between the Gateway wire format and the provider format (`dispatch/transform.py`).
- **Release_Lag**: The elapsed time between a byte being read from upstream and the same (post-decision) byte being sent downstream, exported per request.
- **Inter_Chunk_Timeout**: The bounded maximum time the Gateway waits for the next upstream chunk before terminating the stream (C27).
- **Idle_Timeout**: The bounded maximum time a downstream client may stop reading before the stream is terminated (C27).
- **Write_Timeout**: The bounded maximum time a single downstream write may block before the stream is terminated (C27).
- **Max_Stream_Duration**: The bounded maximum wall-clock lifetime of a single stream (C24), derived on `ResourceContract`.
- **FAIL_OPEN**: Forwarding raw (unscanned/undecided) bytes when a guard, count, or decision cannot be computed. This is forbidden; the Gateway fails closed by withholding.
- **HoldbackMetrics_Split**: The existing producer-only, label-free metrics convention (`runtime/holdback_metrics.py`): zeros-not-absence, percentiles computed on read, publisher separate. GW12's per-request exports follow this split.

## Requirements

### Requirement 1: SSE state machine and codec

**User Story:** As an OpenAI SDK client developer, I want the Gateway to emit SSE frames the official SDKs parse and to tolerate split surrogate escapes from upstream, so that streamed responses decode correctly end to end.

#### Acceptance Criteria

1. WHEN the SSE_State_Machine receives a content DownstreamFrame, THE SSE_State_Machine SHALL serialize it as a `data: {json}\n\n` SSE frame whose JSON body is parseable by the official OpenAI Python and Node SDKs.
2. WHEN a stream terminates cleanly, THE SSE_State_Machine SHALL emit the Terminal_DONE_Marker `data: [DONE]\n\n` as the last frame.
3. WHEN the SSE_State_Machine receives a DownstreamFrame whose `error_code` is set, THE SSE_State_Machine SHALL render a declared Error_Frame shape parseable by the official OpenAI SDKs and SHALL emit no content frame after it.
4. WHEN decoding an upstream SSE byte sequence that splits a UTF-16 surrogate escape across chunk boundaries, THE SSE_State_Machine SHALL buffer the partial escape and decode the complete code point once its bytes arrive.
5. IF an upstream SSE byte sequence is malformed and cannot be decoded, THEN THE SSE_State_Machine SHALL terminate the stream with a declared Error_Frame and SHALL NOT report the stream as successful.
6. THE SSE_State_Machine SHALL decode an upstream frame and re-serialize it such that a round-trip of a well-formed content frame produces an equivalent DownstreamFrame (round-trip property).

### Requirement 2: ASGI application assembly

**User Story:** As a platform operator, I want a real ASGI application with routing, lifespan, and health endpoints, so that the Gateway can be deployed, probed, and observed.

#### Acceptance Criteria

1. THE ASGI_App SHALL expose a router, a lifespan handler, a `/metrics` endpoint, and a `/readyz` endpoint, replacing the `app = None` stub in `edge/app.py`.
2. THE ASGI_App SHALL register the streaming chat path with a wired handler at boot.
3. THE ASGI_App SHALL register the responses, misc, mcp, and rag routes as registered-for-later placeholders whose bodies are deferred to GW15–GW18.
4. WHEN all required state dependencies are available, THE `/readyz` endpoint SHALL return a success status.
5. IF a required state dependency is unavailable, THEN THE `/readyz` endpoint SHALL return a non-success status identifying the unavailable dependency.
6. WHEN the `/metrics` endpoint is requested, THE ASGI_App SHALL return the current metrics exposition computed off the serving loop.

### Requirement 3: Error envelope

**User Story:** As a Gateway engineer, I want error envelopes built in exactly one place that renders value-codes to HTTP/SSE, so that the codes-vs-render boundary is preserved and no HTTP object leaks below `edge`.

#### Acceptance Criteria

1. THE Error_Envelope SHALL be the single component (`edge/errors.py`) that renders egress and dispatch value-codes into HTTP or SSE responses.
2. WHEN the Error_Envelope receives an egress or dispatch value-code, THE Error_Envelope SHALL map it to a declared HTTP status and SSE Error_Frame shape without the originating component constructing any HTTP object.
3. THE egress, dispatch, admit, runtime, contracts, and domain modules SHALL return value-codes and SHALL NOT construct `HTTPException` or `JSONResponse` objects.
4. IF a value-code has no declared envelope mapping, THEN THE Error_Envelope SHALL render a declared generic error response rather than forwarding raw internal detail.

### Requirement 4: Bounded coalescer

**User Story:** As a platform operator, I want each stream's buffer bounded by a derived high-water mark, so that memory does not grow with response length and a slow path cannot exhaust the host.

#### Acceptance Criteria

1. THE Coalescer SHALL derive its High_Water_Mark from `ResourceContract.stream_buffer_bytes(active_streams)` where active_streams equals the current count of Active_Streams, and SHALL NOT use any literal high-water, buffer-size, or queue-depth value.
2. WHILE a stream is active, THE Coalescer SHALL hold at most High_Water_Mark bytes buffered for that stream, measured as the sum of unflushed payload bytes held on behalf of that stream.
3. WHEN the buffered bytes for a stream reach or exceed the High_Water_Mark, THE Coalescer SHALL apply backpressure by suspending upstream reads for that stream until buffered bytes fall below the High_Water_Mark.
4. WHILE a stream is active, THE Coalescer SHALL NOT allow that stream's buffered byte count to exceed the High_Water_Mark at any point in the stream's lifetime.
5. IF the derived per-stream High_Water_Mark computes to less than one byte, THEN THE Coalescer SHALL fail closed, refuse to admit the stream, and return an error indicating the stream was rejected due to insufficient derived buffer capacity, without buffering any stream payload bytes.
6. WHEN backpressure has been applied to a stream and that stream's buffered bytes fall below the High_Water_Mark, THE Coalescer SHALL resume upstream reads for that stream.

### Requirement 5: Credit-based flow control

**User Story:** As a platform operator, I want a slow consumer to slow upstream reads and total memory bounded to a derived number, so that aggregate buffering is predictable and exported.

#### Acceptance Criteria

1. WHILE a downstream consumer's cumulative consumed bytes are less than the stream's cumulative upstream-produced bytes AND the stream's outstanding Credit is zero, THE Credit_Flow_Control SHALL perform zero additional upstream reads for that stream until Credit is next granted.
2. WHEN a downstream consumer consumes N previously released bytes, THE Credit_Flow_Control SHALL grant that stream additional Credit of exactly N bytes.
3. WHILE no previously released bytes have been consumed since the last Credit grant, THE Credit_Flow_Control SHALL grant zero additional Credit to that stream.
4. THE Gateway SHALL bound total streaming memory across all Active_Streams to `Active_Streams × per-stream ceiling`, where the per-stream ceiling derives from `ResourceContract.stream_buffer_bytes(active_streams)` and no stream's buffered bytes exceed that per-stream ceiling.
5. IF a stream's buffered bytes would exceed the per-stream ceiling derived from `ResourceContract.stream_buffer_bytes(active_streams)`, THEN THE Credit_Flow_Control SHALL withhold further upstream reads for that stream and SHALL retain already-buffered bytes without discarding them.
6. WHEN the count of Active_Streams changes, THE Gateway SHALL export the derived `Active_Streams × per-stream ceiling` memory bound as a numeric value in bytes.
7. THE Credit_Flow_Control SHALL conserve Credit such that, at every observation point, outstanding Credit bytes plus consumed Credit bytes equals granted Credit bytes with zero deviation (credit conservation invariant).

### Requirement 6: Real cancellation on client disconnect

**User Story:** As a platform operator, I want client disconnect to abort the upstream request and in-flight guard work within a measured bound, so that resources are not spent on a client that has gone.

#### Acceptance Criteria

1. WHEN the ASGI receive channel reports a Client_Disconnect for an active stream, THE Gateway SHALL abort the in-flight ProviderClient request for that stream and SHALL stop reading further upstream bytes for that stream.
2. WHEN a Client_Disconnect is detected for an active stream, THE Gateway SHALL signal the existing `StreamPipeline.killed()` seam, and the signalled stream SHALL cease in-flight guard work no later than the next chunk boundary.
3. WHEN a Client_Disconnect is detected, THE Gateway SHALL complete the In_Flight_Kill of both ProviderClient and guard work within the cancellation bound derived from `ResourceContract` and SHALL NOT compare against or store any literal interval value.
4. IF the elapsed cancellation interval for a stream exceeds the cancellation bound derived from `ResourceContract`, THEN THE Gateway SHALL force-release that stream's provider connection and buffered state and SHALL record the stream as a bound-exceeded cancellation.
5. WHEN cancellation of a stream completes, THE Gateway SHALL measure the elapsed cancellation interval, in milliseconds, from Client_Disconnect detection to completion of the In_Flight_Kill and SHALL export that interval as a per-cancelled-stream metric.
6. WHEN cancellation of a stream completes, THE Gateway SHALL release that stream's buffered bytes and return its Credit to the available pool, such that the released byte count equals the stream's buffered byte count at the time of Client_Disconnect and no buffered bytes for that stream are forwarded downstream.

### Requirement 7: No post-first-byte fallback splice

**User Story:** As a security engineer, I want fallbacks forbidden after the first content byte, so that a client never receives two concatenated responses.

#### Acceptance Criteria

1. WHILE the First_Byte_Latch is unset, THE Gateway SHALL be permitted to run a signed safe retry or fallback.
2. WHEN the first content byte is released downstream, THE Gateway SHALL set the First_Byte_Latch.
3. WHILE the First_Byte_Latch is set, IF the upstream request fails, THEN THE Gateway SHALL either terminate the stream cleanly or emit a declared Error_Frame and SHALL NOT splice a fallback response.
4. THE Gateway SHALL NOT concatenate bytes from two upstream responses into a single downstream stream (no-splice invariant).

### Requirement 8: Dispatch layer (provider client, routing, transform)

**User Story:** As a Gateway engineer, I want an injectable, abortable upstream source with deterministic routing and byte-verified transforms, so that the egress loop has a provider to read from and cancellation has a request to abort.

#### Acceptance Criteria

1. THE ProviderClient SHALL expose a streaming request interface that the egress loop reads from and that supports abort.
2. WHEN the Gateway aborts a ProviderClient request, THE ProviderClient SHALL stop reading from the upstream source for that request.
3. WHEN given a resolved plan, THE Dispatch_Router SHALL select the same upstream destination deterministically for identical plan inputs.
4. THE Transform SHALL convert between the Gateway wire format and the provider format such that a round-trip of a well-formed request/response produces an equivalent payload (byte-verified round-trip property).
5. THE ProviderClient SHALL be injectable such that local tests substitute a stub provider without a real network socket.
6. WHERE a real provider socket is required, THE Gateway SHALL record that path as a deferred live gate and local tests SHALL exercise the stub-backed path.

### Requirement 9: Bounded stream timeouts (C27)

**User Story:** As a platform operator, I want inter-chunk, idle, and write timeouts, so that a stalled client or provider cannot hold a stream open past drain.

#### Acceptance Criteria

1. THE ResourceContract SHALL derive an Inter_Chunk_Timeout, an Idle_Timeout, and a Write_Timeout, and these SHALL be the only module (`runtime/resources.py`) holding their capacity literals.
2. IF the next upstream chunk does not arrive within the Inter_Chunk_Timeout, THEN THE Gateway SHALL terminate the stream with a declared Error_Frame.
3. IF a downstream client stops reading for longer than the Idle_Timeout, THEN THE Gateway SHALL terminate the stream and release its resources.
4. IF a single downstream write blocks longer than the Write_Timeout, THEN THE Gateway SHALL terminate the stream and release its resources.
5. THE Gateway SHALL derive each timeout from `ResourceContract` and SHALL NOT use a literal timeout value outside `runtime/resources.py`.

### Requirement 10: In-flight control and maximum stream duration (C24)

**User Story:** As a security operator, I want a maximum stream duration and signed in-flight response to kill, revocation, and plan change, so that a long-lived stream respects control-plane decisions made after it started.

#### Acceptance Criteria

1. THE ResourceContract SHALL derive a Max_Stream_Duration and SHALL be the only module holding its capacity literal.
2. WHEN a stream's wall-clock lifetime reaches the Max_Stream_Duration, THE Gateway SHALL cut the stream via the `InFlightKill.CUT_NEXT_CHUNK` seam within a Max_Cut_Latency derived from the ResourceContract and emit a declared Error_Frame indicating the stream was terminated for exceeding the maximum duration.
3. WHEN the org kill switch is engaged for an in-flight stream, THE Gateway SHALL cut the stream via the `InFlightKill.CUT_NEXT_CHUNK` seam within a Max_Cut_Latency derived from the ResourceContract and emit a declared Error_Frame indicating the stream was terminated by the kill switch.
4. WHEN the API key for an in-flight stream is revoked, THE Gateway SHALL cut the stream via the `InFlightKill.CUT_NEXT_CHUNK` seam within a Max_Cut_Latency derived from the ResourceContract and emit a declared Error_Frame indicating the stream was terminated due to key revocation.
5. WHEN the plan for an in-flight stream changes mid-stream, THE Gateway SHALL cut the stream via the `InFlightKill.CUT_NEXT_CHUNK` seam within a Max_Cut_Latency derived from the ResourceContract and emit a declared Error_Frame indicating the stream was terminated due to a plan change.
6. IF a kill-switch, revocation, or plan-change snapshot is unavailable, or its age exceeds a Max_Snapshot_Age derived from the ResourceContract, THEN THE Gateway SHALL treat the snapshot as stale, fail closed, and cut the stream via the `InFlightKill.CUT_NEXT_CHUNK` seam rather than continue forwarding, and emit a declared Error_Frame indicating a fail-closed termination.
7. WHEN the Gateway cuts an in-flight stream via the `InFlightKill.CUT_NEXT_CHUNK` seam, THE Gateway SHALL forward no further upstream chunk bytes to the caller after the cut point.

### Requirement 11: Mid-stream provider error-frame scanning

**User Story:** As a security engineer, I want provider mid-stream error frames scanned before forwarding, so that a secret carried in an error frame is redacted or withheld.

#### Acceptance Criteria

1. WHEN the ProviderClient emits a mid-stream error frame, THE Gateway SHALL scan that error frame through the injected output scanner before forwarding any of its bytes downstream.
2. WHEN the output decision for a scanned mid-stream error frame is redact, THE Gateway SHALL forward the error frame with the decided redactions applied.
3. WHEN the output decision for a scanned mid-stream error frame is block, THE Gateway SHALL withhold the error frame and emit a declared Error_Frame.
4. IF scanning a mid-stream error frame fails, THEN THE Gateway SHALL withhold the error frame and emit a declared Error_Frame (fail-closed-on-scan-error property).
5. THE Gateway SHALL scan a mid-stream error frame in time linear in the frame's byte length (byte-linearity property).

### Requirement 12: Per-request exports

**User Story:** As a platform operator, I want per-request streaming metrics, so that I can observe detector invocations, release lag, and buffer high-water for individual streams.

#### Acceptance Criteria

1. THE Gateway SHALL export, per request, the output-detector invocation count, the Release_Lag, and the buffer high-water, following the HoldbackMetrics_Split (producer-only, label-free, zeros-not-absence, percentiles computed on read).
2. THE Gateway SHALL export the output-detector invocation count such that it matches the plan-derived detector schedule for the request.
3. THE Gateway SHALL NOT attach a tenant label to the exported per-request metrics; the trace SHALL answer which stream produced them.
4. IF a per-request export value cannot be computed, THEN THE Gateway SHALL record it as a withholding and SHALL NOT forward raw stream bytes to compensate.

### Requirement 13: Serving-loop discipline (C38)

**User Story:** As a platform operator, I want the serving loop free of long CPU-bound work, so that one stream's scanning cannot starve another stream's latency.

#### Acceptance Criteria

1. THE Gateway SHALL run tokenization and scanning work in GIL-releasing executors rather than inline on the serving loop.
2. THE Gateway SHALL compute metrics exposition off the serving loop.
3. WHEN a CPU burst is injected on a worker mid-stream, THE Gateway SHALL keep other concurrent streams' worst-chunk p99 within the derived latency budget.
4. THE Gateway SHALL treat serving-loop lag as an SLO input.
5. THE stream-forwarding loop SHALL run no single CPU-bound slice longer than the declared slice bound.

### Requirement 14: Layer contract and injection boundary

**User Story:** As a Gateway architect, I want the import-linter layer contract preserved, so that `egress` never imports `detect` and the scanner stays injected.

#### Acceptance Criteria

1. THE egress module SHALL NOT import the detect module; the scanner SHALL remain injected into `StreamPipeline` as a protocol.
2. THE SSE and dispatch wiring that injects the real detect scanner and ProviderClient SHALL live in `dispatch` or `edge`, both above `detect` in the layer contract.
3. THE codebase SHALL satisfy the import-linter layer order edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain with no egress→detect edge.

### Requirement 15: Fail-closed everywhere

**User Story:** As a security engineer, I want the pipeline to fail closed everywhere, so that no raw bytes are forwarded when a guard, count, or decision cannot be computed.

#### Acceptance Criteria

1. THE Gateway SHALL NOT forward raw upstream bytes when an output decision cannot be computed.
2. IF any guard, count, or decision required to release bytes cannot be computed, THEN THE Gateway SHALL withhold the affected bytes and emit a declared Error_Frame.
3. THE Gateway SHALL expose a FAIL_OPEN counter pinned at zero and SHALL NOT increment it (no-FAIL_OPEN property).

### Requirement 16: Local verifiability and deferred gates

**User Story:** As a developer without cloud resources, I want every GW12 behavior verifiable locally, so that cloud/live gates have a representative local equivalent.

#### Acceptance Criteria

1. THE test suite SHALL verify the memory-bound invariant with N in-process streams over the injected harness, asserting the `Active_Streams × stream_buffer_bytes` bound holds and buffered memory does not grow (local equivalent of LGW12-1).
2. THE test suite SHALL verify SDK conformance over an in-process ASGI transport using recorded-frame checks for the Terminal_DONE_Marker, the Error_Frame shape, and split-surrogate decoding (local equivalent of LGW12-7).
3. THE spec SHALL record the 1,000-stream memory plateau, the real-TCP SDK conformance, and any 200 RPS / fleet-certification run as deferred cloud gates with the scaled-down local equivalent named.
4. THE property tests SHALL use a seeded `random.Random` driven for at least 10,000 iterations without the hypothesis library, running async paths via `asyncio.run`.
5. THE new value types SHALL be frozen slotted dataclasses or StrEnum, SHALL use `MappingProxyType` for module-level tables, and SHALL hold no capacity literal outside `runtime/resources.py`.

## Correctness Properties (for property-based testing)

These properties drive the design's test strategy. Each is testable with a seeded `random.Random` over ≥10,000 iterations (house idiom; no hypothesis), async paths via `asyncio.run`.

1. **Memory-bound invariant** (R4.4, R5.3): at every observation point, total buffered bytes ≤ `Active_Streams × stream_buffer_bytes(active_streams)`. Invariant.
2. **Credit conservation** (R5.5): outstanding Credit + consumed Credit = granted Credit at every observation point. Invariant.
3. **No-post-first-byte-splice** (R7.4): once the First_Byte_Latch is set, the downstream stream contains bytes from at most one upstream response. Invariant / metamorphic (splice attempt must not change the first response's bytes).
4. **Cancellation-within-bound** (R6.3, R6.5): for any disconnect injected at any chunk boundary, cancellation completes within the derived bound and resources return to baseline. Invariant with a derived upper bound.
5. **Byte-linearity of mid-stream scan** (R11.5): scan time of a mid-stream error frame is bounded linearly in its byte length. Metamorphic (`cost(2n) ≈ 2·cost(n)` within tolerance).
6. **Fail-closed-on-scan-error** (R11.4, R15.2): for any injected scan error, no raw bytes of the affected frame are forwarded. Error-condition property.
7. **No-FAIL_OPEN** (R15.3): across all generated inputs including every injected fault, the FAIL_OPEN counter stays zero. Invariant.
8. **SSE codec round-trip** (R1.6, R8.4): decode-then-encode of a well-formed content frame, and transform round-trip of a well-formed payload, produce an equivalent object. Round-trip property (always test for parsers/serializers).
9. **Split-surrogate confluence** (R1.4): decoding an upstream byte stream produces the same code points regardless of where the chunk boundaries fall. Confluence.
10. **Timeout termination** (R9.2–R9.4): for any stall exceeding a derived timeout, the stream terminates with a declared Error_Frame and releases its resources. Error-condition property.
