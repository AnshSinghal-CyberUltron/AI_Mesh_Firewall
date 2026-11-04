# GW34 exit note: SDK and framework compatibility (local half)

Branch `gw34-sdk-compat` (not pushed). Offline gates and exact results: `.agents/tasks/gw34/verification.md`.
Deviations from design.md (all recorded in design.md `## Deviations`): `.agents/tasks/gw34/deviations.md`.

## What now works (offline)

* **Request side.** `tools`, `tool_choice`, `parallel_tool_calls`, `response_format`, tool-role messages
  and assistant `tool_calls` history are validated against a closed schema-keyword table, folded into the
  scanned input channels and rendered upstream ONLY from the template (verified slot deltas, ALLOW-scanned
  names/ids/structural strings, closed literals). Names, ids and structural schema strings are BLOCK-only
  (a REDACT-class value there is `input_blocked`, zero provider calls). `functions`, `function_call`,
  `logprobs`/`top_logprobs` (true), non-text parts, `audio`, `modalities`, `prediction`,
  `web_search_options` stay declared 400s.
* **Both directions.** Assistant arguments are canonicalised (decoded, compact) AND the raw string is
  scanned; streamed and non-stream tool-call arguments get one scan stream per call; the edge assembler
  validates id/name/JSON, scans the decoded form, withholds every call until `finish()` under an enforcing
  rule, and drops them (zero tool bytes, no `finish_reason: "tool_calls"`) on any egress terminate.
* **Framing.** One stable `id`/`model`/`created`, `role` once, merged deltas, a final `finish_reason` chunk,
  `x-request-id` on the 200 start.
* **Pre-first-byte refusals** (admission, budget, input BLOCK, tools validation, upstream failure,
  first-window output BLOCK, assembler termination) are plain HTTP errors with the non-stream bytes;
  Responses keeps today's bytes (`plain_pre_byte=False`).
* **Cancellation.** A disconnect watcher cancels only the inner stream task (never the handler); provider
  iterator closed, one release, one audit record, `stream_killed`.
* **C26.** A realistic tools request is no longer refused by the part-count pre-check; a tool-free flood
  still is.

## L34-1 matrix result (host environment, loopback TCP; NOT a real v2 boot)

Report: `sdk-matrix-report.json` (written by `sdk_matrix/conftest.py`).

| client | ran | passed | skipped | known failure |
|---|---|---|---|---|
| python-sync (openai 3.42.1) | 11 | 11 | 0 | none |
| python-async (openai 3.42.1) | 11 | 11 | 0 | none |
| node (openai 4.104.0) | 10 | 9 | 0 | cell `c` xfail (see below) |

Plus the wire-mode cells (real `OpenAIUpstreamProvider` over a loopback fake upstream with seeded random
byte splits, 12 variants), the D8 `ASGITransport` buffering probe, `/v1/files` 404 (exactly one request),
and 14 wire profiles. Closing rule satisfied: Python SDK and Node loopback cells have `ran > 0`.
**L34-1 against a real v2 boot is NOT claimed.**

## Open findings (owner decisions; nothing below was changed)

1. **`amf.trace` vs the stock SDKs (requirement R1.6 cannot be met as written).** Python yields the trace
   as a bare trailing `ChatCompletionChunk` (no `choices`, no id; the `.stream()` helper ignores it). Node
   yields `{event, data}` and its `chat.completions.stream()` helper throws "chunk.choices is not iterable".
   Fixing it changes the trace framing consumers see (SSE comment, or an in-band `choices: []` chunk like
   OpenAI's usage chunk). Node cell `c` is `xfail(strict=True)`. Options and effect were sent to the owner.
2. **Tool-call counters** `tool_calls_released/withheld` are on `RequestHolds` and in the audit `detail`
   (digits) but are NOT exported as new Publisher series (pinned `SERIES_COUNT`, R15). Follow-up.
3. **Pre-existing:** `tests/gates/test_check_tenant_scale.py` fails on the untouched tree
   (`pinecone_transport.py:140`); `request_pre_check_exceeded` renders HTTP 500 (unmapped code). Both unchanged.

## Slot-usage and cost evidence

* Measured detector cost (dev `DeterministicFindings`): p50 80.4 us / p99 152.1 us per short slot,
  491.6 us p99 for 64-char worst-case slots (`scan-cost.json`). `_SCAN_SLOT_COST_US = 200`,
  `max_scanned_slots = 50` at the 20 ms reference.
* 20-tool pydantic request with `$defs/$ref/anyOf/additionalProperties:false` and a 20-turn tool history:
  **8 of 50 slots (16%)**, required target <= 70% MET. 40-tool request: **8 of 50 (16%)**, stretch target
  <= 80% MET. This relies on joined runs (deviation V1), not on a cheaper detector.
* No capacity literal outside `runtime/resources.py`; `check_capacity_literals.ALLOWED_REL` unchanged
  (`resources_tools.py` / `resources_budget.py` / `resources_stream.py` hold derivations only).
  `runtime/resources_org.py` (GW27 D-13) is neither used nor duplicated.

## Properties (seeded `random.Random`, >= 100 iterations, tagged in-source `# Feature: gw34-sdk-compat, Property N:`)

P1 (150, also against the real SDK `accumulate_delta`), P2 (150), P3 (120), P4 (300 assembler + 100
request side), P5 (100), P6 (300), P7 (150 assembler, 120 egress), P8 (200 corpus).

## Deferred, not claimed

Live Docker/staging L34-1 against a real v2 boot; L34-2 live; real framework-library runs (LangChain,
LlamaIndex, Vercel AI SDK, LiteLLM, Azure client); Node against real TCP beyond the loopback harness.
AGENTS.md was left untouched.
