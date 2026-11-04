# Design review: GW34 SDK and framework compatibility (round 5)

Reviewed: `design.md` (573 lines) against `requirements.md` and the code in `.worktrees/gw34/gateway_v2`.

## Verdict: CHANGES_REQUESTED (4 MEDIUM, 2 LOW)

The round-4 findings are fixed. The remaining problems are in the interaction between new components and shipped code, found by reading `routes.py`, `egress/stream.py`, `provider.py` and the existing test harnesses.

## Findings

### 1. MEDIUM: `finish()` has no precondition for a stream that already terminated

Where: "Assembler API, end of stream, and termination mechanism", step 3: "After `pipeline.run(...)` returns cleanly, `_attempt()` does `await assembler.finish()`".

`egress/stream.py` does not raise on a terminal failure. `_terminate(...)` (output_blocked, scan_failure, holdback_overflow, killed) sends a `DownstreamFrame(error_code=...)` and returns `StreamStats` with `incomplete=True`. So `run` "returns cleanly" in exactly the cases where it must not release anything. With an enforcing rule all tool calls are still withheld at that point. As written, `finish()` would run `decoded_clean`, then write the withheld calls:

- Started stream: the shipped `_sink` drops frames once `sent_terminal` is set, so this is accidental, not designed.
- Pending stream (the new deferred-start path): `_sink` only records the code and "writes nothing". The design does not say it sets `sent_terminal`. `finish()` could then start a `200 text/event-stream` and release tool calls after a recorded refusal. That breaks R7.3 and R4.8.

Fix: state the guard explicitly and test it:
```python
produced.stats = await pipeline.run(chunks, sink, kill_latch)
if (not produced.stats.incomplete
        and kill_latch.reason() is None
        and pending_error.code is None
        and not sent_terminal.is_set()):
    await assembler.finish()   # may itself Terminate
```
Add a test per egress terminate code (`output_blocked` on a content channel, `scan_failure`, `holdback_overflow`, `killed`) with a withheld tool call outstanding. Each must release zero tool bytes, give no `finish_reason: "tool_calls"`, and in the pending state give a plain HTTP error.

### 2. MEDIUM: assembler buffers are not bounded by the stream buffer budget

Where: "Tool-call assembler" (enforcing withholds all calls to `finish()`; arguments buffered "bounded by `arguments_bytes`"), D9 (JSON-mode `content` "buffered and released once at the end"), "ToolBounds values".

The only bound is per call: `arguments_bytes = max_request_bytes() // 4`, with up to `max_tool_calls = 128` calls. That allows 32x `max_request_bytes()` per stream. D9 JSON-mode content buffering has no bound at all. The shipped pipelines charge held bytes against `contract.stream_buffer_bytes(active_streams())` (`_build_pipeline` passes it as `max_buffer_bytes`; `Coalescer` admits against it). The assembler sits outside that accounting, so tool-call and JSON-mode streams can hold unbounded memory per stream. That is the failure mode R2-05 and GW12 closed. It also breaks R10.2, which requires every new bound to be a derived contract method.

Fix:
- Add `ToolBounds.max_withheld_bytes`, derived from `stream_buffer_bytes(active_streams())`. This is the total across all calls plus JSON-mode content.
- The assembler tracks one running total. Exceeding it gives `Terminate(stream_malformed_upstream)`, or `output_blocked` for JSON mode. Pick one and state it.
- Charge the same bytes to the request's `Coalescer`, or state why a separate bound is safe.
- Add a test with 128 calls at `arguments_bytes` each, expecting a terminal code with a bounded peak.

### 3. MEDIUM: the disconnect watcher conflicts with the shipped test ASGI harnesses

Where: D14 and "Cancellation (R6)".

The watcher awaits `receive()` and cancels the inner stream task on `http.disconnect`. Many shipped streaming tests use a `receive` stub that returns `{"type": "http.disconnect"}` as soon as the request body list is exhausted, for example `tests/edge/test_v2_leaks_d_lifecycle.py:480` and `tests/edge/test_v2_leaks_f_properties.py:439` (the latter drives streams), plus `test_tenant_scale_harness.py:78` and `test_gw16_*`. Under the new design that is a client that leaves before the first frame. The watcher cancels the stream, and those tests fail or assert the wrong audit outcome. The design only tests a disconnect after the final send. Its "Existing tests to update" list is driven by the SSE grep and does not include these files.

Fix: choose one rule and write it down.
- Add a shared test helper `receive_until_sent(send_done_event)` that blocks until the response completes, then returns `http.disconnect`. This matches uvicorn and httpx behaviour.
- Convert every harness found by `grep -rn "http.disconnect" tests` that reaches a streaming route. Add them to the R7.8/R6 task list by name.
- Add an explicit decision for a `receive` that returns `http.disconnect` before any send: it is a real early cancel, with a test of the expected `stream_killed` audit outcome.

### 4. MEDIUM: the tools-disabled fake-contract path still calls a missing contract method

Where: "Decoder" (lazy `tool_bounds`, fakes "need no edit", "a `tools` request on it is refused, never a 500") versus "C26 part-count allowance (D20)".

`_pre_checks` runs before parse and, for a body containing `tools`, `tool_calls` or `response_format`, calls `contract.max_request_parts_with_tools()`. The standalone fakes in `tests/edge/test_lgw15_*` are not `ResourceContract` subclasses and only define the shipped accessors. `max_request_parts()` exists at `openai_chat.py:336`. The new method does not. So the promised test fails with an `AttributeError`, which is a 500. The `tool_bounds is None` rule is applied only at the decoder and the fold.

Fix: in `_pre_checks`, pick the ceiling from the route-level `self.tool_bounds`. If it is `None`, always use `contract.max_request_parts()`. Compute the allowance from the resolved bounds (`max_request_parts() + _PARTS_PER_SCHEMA_NODE * bounds.schema_nodes`) instead of a second contract method. State this in D20 and extend the fake-contract test to send a body containing the bytes `tools`.

### 5. LOW: the scripted harness does not match `StubProviderClient`

Where: "L34-1 matrix", `harness.py`: "the shipped `StubProviderClient` events; the script is chosen by a marker in the last user message so one app serves every cell".

`StubProviderClient` replays one fixed `script` for every `open()` and has a one-way `_aborted` latch (`dispatch/provider.py`). One shared instance cannot select by marker, and after the first abort it yields nothing, which would break R6.2 (second request after cancel succeeds). It also exposes no "iterator closed" observation for R6.1.

Fix: the harness defines a `ScriptedProvider` whose `open(request)` selects a script from `request.body["messages"]` and creates a fresh per-call state. It records `closed` and `aborted` counters per call. Say that it is new test code, not the shipped stub.

### 6. LOW: `_HELD_FRAME_SLOTS = 2` is a capacity bound outside `runtime/resources.py`

Where: "Deferred start".

`check_capacity_literals` is name-based and will not flag it, but R10.2 says no new bound outside the contract. The justification, "structural count, not a capacity", is a judgement the gate cannot check.

Fix: either derive it in `ResourceContract` (a held-frame count with a documented rationale), or define it as a tuple of two named slots (`_ROLE_SLOT`, `_FINISH_SLOT`) so no integer bound exists.

## Verified assumptions

- Module sizes match the design's baseline (`resources.py` 800, `routes.py` 799, `egress/stream.py` 800, `sse.py` 772, `openai_chat.py` 712). `check_sizes` limits are 800 per module and 120 per function.
- `check_capacity_literals` is name-based (`CAPACITY_KEYWORDS`, `Semaphore`/`Queue` calls) and exempts only `runtime/resources.py`. The new constants as designed do not trip it.
- `scan_input` pools findings in request-content coordinates, with `_base_offsets` a separator-free running sum. `Finding` has spans but no channel, so the span-to-pair attribution (D23) is needed and feasible.
- `_drain` in `dispatch/openai_upstream.py` drops only the `role` channel and delays one frame to set `final`. The design's claims about pass-through and the tail frame are correct.
- `ChatRoute._stream`, `_finish_stream`, `_next_attempt`, `_attempt` and `_ProducedModeHolder` exist as described. Errors reach the route through the `_sink` `error_code` frame, as D7 assumes. `_attempt` returns `latch.is_set()` to `run_with_no_splice`.
- `max_completions()` is a static method on `ResourceContract`. The mixin move keeps it callable on the class, as `resources.py` line 622 does.
- Python is 3.12 (`requires-python >= 3.12`), so `Task.cancelling()` in D22 is available.
- `node/package-lock.json` pins `openai` 4.104.0. `pyproject.toml` has the `conformance` and `serve` extras. `tests/openai_conformance/node` and `stub_server.py` are ignored by pytest as the design says.
- `OpenAIResponseRenderer._message_for` concatenates all `output_tool_arguments` into one `tool_calls` entry, as gap 4 states.
- Layering, capacity-literal gate and frozen-dataclass rules are respected by the file plan. `edge/wire` stays pure and egress gains only a `domain.channels` import.
- Split streaming deltas are covered by design: the per-index scan key `output_tool_arguments#n` gives each call its own egress `_Text` stream, and the raw-plus-decoded scan happens at `finish()`.
- Request-side arguments are scanned in both raw and canonical-decoded form, with BLOCK-only channels for names, ids and structural schema strings.

## Unverified or wrong assumptions

- Wrong: "after `pipeline.run(...)` returns cleanly" implies success. Egress returns normally with `incomplete=True` after a terminal frame (finding 1).
- Wrong: the fake contracts "need no edit". `_pre_checks` calls a method they lack (finding 4).
- Wrong or untested: a disconnect only arrives after the response completes. Several shipped harnesses return `http.disconnect` immediately after the body (finding 3).
- Wrong: `StubProviderClient` can serve multiple scripts and survive an abort (finding 5).
- Not verifiable offline: whether the shipped detector stays under 20 us per slot. The design makes this a first-task measurement with a stated fallback, which is acceptable.
- Not verified, accepted as design intent: the `httpx.ASGITransport` buffering claim (D8) is backed by a planned probe test. The SDK image build needs the network, and the closing rule covers that.
