# Requirements Document

## Introduction

On streaming chat requests (`POST /v1/chat/completions` with `stream=true`, OpenAI-compatible SSE), the AI Mesh Firewall gateway reports a "firewall tax" (the addon latency the gateway adds on top of the model) that is computed from the streaming pipeline trace. Today that number is anchored on the wrong epoch: streaming latency is measured from `provider_start_ts` (the moment the upstream provider's SSE stream begins) rather than from the request-accept epoch (the moment the gateway accepts the request, captured pre-stream after preflight and model selection).

Because both `ttft_ms` and `duration_ms` anchor on `provider_start_ts`, and the reported total is set to `duration_ms`, the addon split (`compute_addon_split`) reduces algebraically to the provider's time-to-first-token (TTFT):

- `total_latency_ms ≈ duration_ms`
- `model_output_ms ≈ duration_ms − ttft_ms`
- `addon (t_addon_pre_ms) = total − model_output − post ≈ ttft_ms`

So the benchmarked firewall tax on a streaming request is literally the customer's model TTFT, not gateway-added latency. This causes the Phase-1 latency gate to pass unconditionally and the stop-and-revise checkpoint never to fire.

This feature re-bases the streaming latency measurement on the request-accept epoch that already exists (`StreamLaunchContext.start_time`, a `time.perf_counter()` value captured pre-stream), so the reported total reflects full wall-clock from request accept to stream close and the addon reflects gateway pre-model time. This is purely a measurement/telemetry correctness change and MUST NOT alter enforcement behavior (block/redact/allow).

This spec covers ONLY the anchoring re-base for the streaming total and its addon split. Other Gate 1 items (reconciliation-clamp/residual-histogram changes, a STREAM SSE bench, microsecond resolution / ran-flag / skip semantics, capacity-vs-tax split, per-stage start/end timestamps) are explicitly out of scope.

## Glossary

- **Gateway**: The AI Mesh Firewall FastAPI service that fronts `POST /v1/chat/completions` and applies the governance pipeline before/after the upstream model.
- **Streaming request**: A `POST /v1/chat/completions` request with `stream=true` that returns Server-Sent Events (SSE) in the OpenAI-compatible format.
- **Request-accept epoch**: The `time.perf_counter()` timestamp captured when the gateway accepts a request, after preflight and model selection but before the provider stream opens. In code this is `StreamLaunchContext.start_time` (field with `default_factory=time.perf_counter`).
- **provider_start_ts**: The `time.perf_counter()` timestamp set in `StreamRunMetrics.provider_start_ts` at the moment the upstream provider's SSE stream begins (in `instrumented_stream_generator`). It excludes all gateway pre-provider stages.
- **TTFT (time-to-first-token)** / **ttft_ms**: The elapsed time from `provider_start_ts` to the first model token (`StreamRunMetrics.ttft_ms = (first_token_ts − provider_start_ts) × 1000`). This is a property of the customer's model, not the gateway.
- **model_output_ms**: The provider's token-generation time (from first token to last token / stream close). On the streaming path this is derived as `duration_ms − ttft_ms`. This meaning is correct and is preserved.
- **total_latency_ms**: The full wall-clock latency reported for the request in `pipeline_trace`, intended to span request-accept to stream close.
- **addon** / **firewall tax** / **t_addon_pre_ms**: The gateway-added pre-model latency, computed by `compute_addon_split` as `total_latency_ms − model_output_ms − t_addon_post_ms`. It is intended to represent time the gateway adds before the model, NOT the model's TTFT.
- **t_addon_post_ms**: Post-model gateway time (output guardrail), sourced from `output_guardrail_ms`.
- **pipeline_trace**: The operator-facing per-request trace object (stages + roots) attached to responses and telemetry; on the streaming path it is attached to the terminal SSE frame and reconciled in `build_stream_trace_frame`.
- **SSE terminal frame**: The final `chat.completion.chunk`-shaped frame (with `choices: []` and an added `zeroshield` object plus `pipeline_trace`) emitted once by `build_stream_trace_frame` immediately before `data: [DONE]`.
- **Non-streaming path**: The `stream=false` request path, whose latency is already anchored on the request wall-clock and is not changed by this feature.
- **Enforcement behavior**: The block / redact / allow / flag decisions the firewall applies to input and output; unrelated to latency measurement.
- **duration_ms**: The provider-stream-only elapsed time (`StreamRunMetrics.duration_ms = (now − provider_start_ts) × 1000`); the span from the provider stream opening to stream close.

## Requirements

### Requirement 1: Anchor streaming total latency on the request-accept epoch

**User Story:** As a firewall operator benchmarking the gateway, I want the streaming request's reported total latency to span from request-accept to stream close, so that the reported number reflects full wall-clock time rather than only the provider's stream duration.

#### Acceptance Criteria

1. WHEN the Gateway finalizes a streaming request AND `StreamLaunchContext.start_time` is set, THE Gateway SHALL compute `total_latency_ms` as the elapsed time from the request-accept epoch (`StreamLaunchContext.start_time`) to stream close, measured in milliseconds and rounded to 0.1 ms.
2. WHEN the Gateway finalizes a streaming request AND `StreamLaunchContext.start_time` is set, THE Gateway SHALL include the gateway pre-provider stage time (elapsed time from `StreamLaunchContext.start_time` to `StreamRunMetrics.provider_start_ts`) within the streaming `total_latency_ms`.
3. WHERE a streaming request incurs a gateway pre-provider delay of 1.0 ms or greater, THE Gateway SHALL report `total_latency_ms` that exceeds `duration_ms` (the provider-stream-only duration) by the measured pre-provider delay, within a tolerance of 0.1 ms.
4. WHERE a streaming request incurs a gateway pre-provider delay of less than 1.0 ms, THE Gateway SHALL report `total_latency_ms` that equals `duration_ms` within a tolerance of 1.0 ms.
5. IF `StreamLaunchContext.start_time` is missing or unset when the Gateway finalizes a streaming request, THEN THE Gateway SHALL set `total_latency_ms` equal to `duration_ms`, report a non-negative `total_latency_ms` value, and complete finalization without raising an error.

### Requirement 2: The addon reflects gateway pre-model time, not TTFT

**User Story:** As a firewall operator, I want the reported firewall tax (addon) to reflect gateway-added pre-model latency, so that the benchmark measures the firewall and not the customer's model time-to-first-token.

#### Acceptance Criteria

1. WHEN `compute_addon_split` runs for a streaming request, THE Gateway SHALL compute the addon (`t_addon_pre_ms`) as `total_latency_ms` minus `model_output_ms` minus `t_addon_post_ms`, where `total_latency_ms` is anchored on the request-accept epoch (the timestamp at which the Gateway accepts the inbound request).
2. IF a streaming request has gateway pre-model time of `X` ms and provider TTFT of `Y` ms where `X` and `Y` differ by more than 1 ms, THEN THE Gateway SHALL report an addon within ±1 ms of `X` and SHALL NOT report an addon within ±1 ms of `Y`.
3. THE Gateway SHALL compute the reported addon such that it does not reduce algebraically to `ttft_ms` for any input where `total_latency_ms` exceeds `ttft_ms` by more than 1 ms.
4. WHERE the gateway pre-provider time is 0 to 1 ms, THE Gateway SHALL report an addon between 0 ms and 1 ms rather than a value within ±1 ms of the provider TTFT.
5. IF `total_latency_ms`, `model_output_ms`, or `t_addon_post_ms` is unavailable or the computed addon is negative, THEN THE Gateway SHALL report an addon of 0 ms and SHALL emit an indication that the addon could not be computed, without discarding the remaining latency measurements.

### Requirement 3: TTFT remains a separate reported field

**User Story:** As a firewall operator, I want TTFT to remain visible as its own field, so that I can still observe provider latency without it being conflated with the firewall tax.

#### Acceptance Criteria

1. WHEN a streaming request completes its provider stream, THE Gateway SHALL report `ttft_ms` as a distinct field, separate from `t_addon_pre_ms`, in both the streaming `pipeline_trace` and the telemetry record.
2. THE Gateway SHALL compute `ttft_ms` as the difference in milliseconds between `first_token_ts` and `provider_start_ts`, independent of the request-accept epoch.
3. IF `provider_start_ts` or `first_token_ts` is missing or unset for a streaming request, THEN THE Gateway SHALL report `ttft_ms` as null and SHALL NOT substitute the request-accept epoch or any other timestamp, and SHALL record an indication that provider stream timing was unavailable.
4. WHILE reporting a streaming request whose gateway pre-model time differs from its provider TTFT by 1 millisecond or more, THE Gateway SHALL report `t_addon_pre_ms` and `ttft_ms` as unequal values and SHALL NOT set `t_addon_pre_ms` equal to `ttft_ms`.

### Requirement 4: model_output_ms continues to mean provider generation time

**User Story:** As a firewall operator, I want `model_output_ms` to keep meaning provider token-generation time, so that the addon split remains interpretable after the anchoring change.

#### Acceptance Criteria

1. THE Gateway SHALL derive streaming `model_output_ms` as `duration_ms − ttft_ms`, where `duration_ms` is the provider generation elapsed time and `ttft_ms` is the provider time-to-first-token, both measured in milliseconds.
2. THE Gateway SHALL exclude all gateway pre-provider stage time from `model_output_ms`, such that `model_output_ms` reflects only provider token-generation time.
3. WHEN the streaming total is re-anchored on the request-accept epoch, THE Gateway SHALL compute `model_output_ms` to a value identical (within 0 ms) to its value before re-anchoring.
4. IF `ttft_ms` exceeds `duration_ms` or either value is unavailable, THEN THE Gateway SHALL set `model_output_ms` to 0 and record an indication that provider generation time could not be derived.

### Requirement 5: Backward-compatible pipeline_trace shape

**User Story:** As a downstream consumer of the pipeline trace (operator UI, telemetry drain), I want the trace key shape to stay unchanged, so that existing consumers keep working after the anchoring fix.

#### Acceptance Criteria

1. WHEN the Gateway emits a streaming `pipeline_trace` object after the anchoring change, THE Gateway SHALL include every key present in the pre-change trace, including at minimum `total_latency_ms`, `ttft_ms`, `model_output_ms`, `stage_latency_sum_ms`, `overhead_ms`, `t_addon_pre_ms`, `t_addon_post_ms`, and `t_t2_ms`.
2. WHEN the Gateway emits a `pipeline_trace` object after the anchoring change, THE Gateway SHALL retain each existing field under its original key name (zero renamed keys).
3. WHEN the Gateway emits a `pipeline_trace` object after the anchoring change, THE Gateway SHALL retain every existing field (zero removed keys) such that the emitted key set is a superset of the pre-change key set.
4. WHEN the Gateway emits a `pipeline_trace` object after the anchoring change, THE Gateway SHALL preserve the value type of each existing field, keeping each of `total_latency_ms`, `ttft_ms`, `model_output_ms`, `stage_latency_sum_ms`, `overhead_ms`, `t_addon_pre_ms`, `t_addon_post_ms`, and `t_t2_ms` as a numeric value.
5. WHEN the Gateway emits the SSE terminal frame after the anchoring change, THE Gateway SHALL emit a `chat.completion.chunk` containing a `choices` field equal to an empty array, a `zeroshield` object, and a `pipeline_trace` object, with no added, removed, or renamed top-level keys relative to the pre-change terminal frame.

### Requirement 6: Non-streaming path is unchanged

**User Story:** As a firewall operator, I want the non-streaming latency measurement to stay exactly as it is, so that this fix targets only the streaming defect.

#### Acceptance Criteria

1. THE Gateway SHALL leave the non-streaming request latency measurement byte-for-byte unchanged by this feature, such that the computed values of `total_latency_ms` and `model_output_ms` for any given non-streaming request are identical before and after this feature is applied.
2. WHEN the Gateway processes a non-streaming request, THE Gateway SHALL compute `total_latency_ms` from the non-streaming request wall-clock duration measured from request receipt to response emission, expressed in milliseconds with a non-negative value.
3. WHEN the Gateway processes a non-streaming request, THE Gateway SHALL derive `model_output_ms` from the measured upstream model call duration (`upstream_ms`), expressed in milliseconds with a non-negative value.
4. IF this feature modifies any code path that alters the computed non-streaming `total_latency_ms` or `model_output_ms` for an identical request input, THEN THE Gateway SHALL be considered non-compliant with this requirement.

### Requirement 7: Enforcement behavior is unchanged

**User Story:** As a firewall operator, I want enforcement decisions to be unaffected by the latency measurement change, so that a telemetry fix cannot alter security behavior.

#### Acceptance Criteria

1. THE Gateway SHALL apply the same input enforcement decision (block / redact / allow / flag) for a given streaming request before and after the anchoring change.
2. THE Gateway SHALL apply the same output-guard enforcement decision (block / redact / flag / allow) for a given streaming response before and after the anchoring change.
3. THE Gateway SHALL NOT change the emitted content bytes of a streaming response as a result of the anchoring change.
4. THE Gateway SHALL confine the anchoring change to latency/telemetry computation and SHALL NOT alter the streaming enforcement code paths.

### Requirement 8: Measurable acceptance via a synthetic-timing test

**User Story:** As a firewall engineer, I want an automated test that constructs a stream with a known gateway pre-time and a known provider TTFT, so that I can prove the addon tracks gateway time rather than TTFT.

#### Acceptance Criteria

1. THE Gateway test suite SHALL include a test that constructs a streaming finalization with a synthetic gateway pre-provider time (via a controlled `StreamLaunchContext.start_time`) and a synthetic provider TTFT (via controlled `provider_start_ts` and `first_token_ts`), where the synthetic gateway pre-provider time and the synthetic provider TTFT differ by at least 50 milliseconds.
2. WHEN the synthetic-timing test runs, THE test SHALL assert that the reported `total_latency_ms` equals the full wall-clock duration from the request-accept epoch to stream close within a tolerance of ±5 milliseconds.
3. WHEN the synthetic-timing test runs, THE test SHALL assert that the reported addon `t_addon_pre_ms` equals the synthetic gateway pre-provider time within a tolerance of ±5 milliseconds.
4. IF the synthetic gateway pre-provider time and the synthetic provider TTFT (`ttft_ms`) differ by more than 5 milliseconds, THEN THE test SHALL assert that the reported addon `t_addon_pre_ms` differs from the synthetic provider TTFT (`ttft_ms`) by more than 5 milliseconds.
5. WHEN the synthetic-timing test runs, THE test SHALL assert that the reported `model_output_ms` equals the synthetic provider generation time (`duration_ms − ttft_ms`) within a tolerance of ±5 milliseconds.
6. IF any assertion in the synthetic-timing test does not hold, THEN THE test SHALL fail and report an error indicating which timing value diverged from its expected bound.
7. THE Gateway test suite SHALL complete with zero test failures and zero test errors under the existing gate `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q`.
