# GW34 handoff: SDK and framework compatibility

Branch `gw34-sdk-compat`, worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw34`.
Not pushed. `suraj-revamp` not fast-forwarded. Worktree left in place.

## Rebase result

`git rebase suraj-revamp` reported "Current branch gw34-sdk-compat is up to date." The branch already contains `suraj-revamp` tip `1634da0c` (merge-base equals that tip), so there was nothing to replay and no conflicts.

Post-rebase sanity check (host venv, from `gateway_v2/`):
- `ruff check .`: All checks passed
- `lint-imports`: 3 kept, 0 broken
- GW34 test subset (`tests/{edge,runtime,egress,dispatch}/test_gw34_*` + `tests/openai_conformance/sdk_matrix`): 273 passed, 1 xfailed

## What landed

Spec: `.kiro/specs/gw34-sdk-compat/{requirements,design}.md`. Plan and notes: `.agents/tasks/gw34/`.

- Preflight evidence in `docs/plans/evidence/2026-11-04-gw34/` (baseline, scan-cost, SDK image build).
- Size-driven splits of `runtime/resources.py`, `egress/stream.py`, `edge/wire/sse.py`, `edge/routes.py` (new `resources_budget`, `resources_stream`, `stream_meta`, `sse_chunks`, `sse_decode`, `chat_stream`).
- Tool channels and `ToolBounds` (`domain/channels.py`, `runtime/resources_tools.py`).
- Pure wire layer: tool and `response_format` validation, canonical arguments, fold (`openai_tools`, `openai_fold`, `openai_params`), SSE decoder and encoder, `tool_stream` assembler, renderer with `tool_calls[]`.
- Egress meta-channel pass-through and per-call isolation.
- Edge request side: upstream rendering from the template only, fill verification, BLOCK-only attribution for names and ids, C26 allowance.
- Non-stream tool output validation and JSON-mode content check.
- Deferred-start streaming response and plain-HTTP pre-byte refusal.
- Disconnect watcher and cancellation.
- L34-1 SDK matrix (Python sync/async and Node, cells a to j), L34-2 `/v1/files` 404 test, wire profiles.

## Offline gate results (verbatim from `.agents/tasks/gw34/offline-gate.md`)

Environment: host venv `/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin` (Python 3.12.3), HEAD at the time `d38cadcc`.

```
## Summary
- mypy --strict: PASS; ruff: PASS; import-linter: PASS (3 kept, 0 broken)
- AST gates: capacity_literals, no_module_mutable, frozen_dataclasses, http_outside_edge_resolve, sizes PASS; tenant_scale FAIL (1 finding, file from GW30 commit 564e2ecf, not touched by GW34)
- Full pytest: 1 failed (tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean, same finding), 3321 passed, 104 skipped, 1 xfailed

## $ mypy --strict gateway_v2
Success: no issues found in 238 source files
EXIT 0

## $ ruff check .
All checks passed!
EXIT 0

## $ lint-imports
Analyzed 289 files, 1708 dependencies.
Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT
Contracts: 3 kept, 0 broken.
EXIT 0

## $ python -m lint.check_capacity_literals            EXIT 0
## $ python -m lint.check_no_module_mutable             EXIT 0
## $ python -m lint.check_frozen_dataclasses            EXIT 0
## $ python -m lint.check_http_outside_edge_resolve     EXIT 0

## $ python -m lint.check_tenant_scale
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
EXIT 1

## $ python -m lint.check_sizes                         EXIT 0

## $ python -m pytest -q -p no:cacheprovider (final lines)
----------------- L34-1 matrix (ran/passed per client x cell) ------------------
python-sync   a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
python-async  a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
node          a:1/1 b:1/1 c:0/1 d:1/1 e:1/1 f:1/1 g:1/1 h:1/1 i:1/1 j:1/1
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3321 passed, 104 skipped, 1 xfailed, 9 warnings in 595.99s (0:09:55)
EXIT 1
```

Failure analysis (verbatim): the only failure is check_tenant_scale at `gateway_v2/dispatch/vector_transports/pinecone_transport.py:140`. `git log main..HEAD` on that file shows only 564e2ecf (GW30 Pinecone live-acceptance); GW34 commits do not touch it. Fixing means changing GW30 dispatch code or the gate, which is out of GW34 scope; left unfixed, no baseline added. Decision recorded: accepted as pre-existing for GW34; belongs to the GW30 lane.

## Design deviations (from `.agents/tasks/gw34/deviations.md`; also in design.md `## Deviations`)

- V1: Joined runs instead of one fold pair per string. All strings of one tool channel are joined with `JOIN_SEPARATOR` into one fold pair, split back after the input decision. At most 8 detector calls per request. Reason: measured detector cost ~80 us p50 / 152 us p99, 7x the design's 20 us assumption. The design names widening subtree joining as the permitted mitigation.
- V2: `JOIN_SEPARATOR = "\ue000"` (private use). ASCII `\x1c`-`\x1f` match `\s` and caused a phone-number false positive. A request string containing the separator is refused `malformed_wrong_shape`.
- V3: `_SCAN_SLOT_COST_US = 200` (measured p99 152.1 us rounded up), so `max_scanned_slots = 50`. 20-tool request uses 8/50 (required <= 70%); 40-tool uses 8/50 (stretch <= 80% met).
- V4: `max_withheld_bytes = stream_buffer_bytes(connection_budget())`; the contract has no `active_streams()`. The per-stream ceiling is also enforced by charging the request's own `Coalescer`.
- V5: The assembler renumbers output call indexes dense and ascending in first-emission order. The stock SDK `accumulate_delta` merges by list position, not `index` (found by Property 1).
- V6: `role` in a delta is not malformed (real providers send it); `_drain` drops it as before. Reserved: `finish_reason`, keys containing `#`, keys starting `output_tool_`.
- V7: BLOCK-only attribution escalates REDACT and BLOCK to BLOCK; FLAG (monitor mode) stays FLAG.
- V8: No held role/finish slots. The encoder puts `delta.role` on the first chunk it writes, and empty, role-only or meta-only frames never become chunks. Same externally visible behavior.
- V9: Tool-call counters go to the audit `detail` as digit strings via `RequestHolds.note_tool_calls`. No new Publisher series (would change pinned `SERIES_COUNT`). Follow-up.
- V10: All Python SDK cells use the loopback uvicorn server (sync `httpx.Client` cannot use `ASGITransport`).
- V11: `x-request-id` is now sent on the 200 SSE start.
- V12: `SSEEncoder.content(frame)` emits one merged `choices[0].delta` per frame. Three shipped codec property tests were updated to merge same-channel text.
- V13: R1.6 "`amf.trace` never surfaces as a chunk" is not satisfiable with stock SDKs without changing the trace framing. Python yields it as a bare `ChatCompletionChunk`; Node yields `{event, data}` and `chat.completions.stream()` throws. The matrix filters the trailing trace chunk (Python) and records Node cell `c` as `xfail(strict=True)`. Needs an owner decision.

Pre-existing, left alone: the `check_tenant_scale` finding above; `request_pre_check_exceeded` renders HTTP 500 `gateway_error` (unmapped code).

## What remains for the human

1. Live Docker/staging gate NOT run and not claimed: L34-1 against a real v2 boot and L34-2 live.
2. Decide V13 (`amf.trace` framing). Until then Node matrix cell `c` is a strict xfail (node `c:0/1`).
3. `check_tenant_scale` finding in `dispatch/vector_transports/pinecone_transport.py:140` (GW30). Full pytest has 1 red test until the GW30 lane fixes it.
4. V9 follow-up: publish `tool_calls_released` / `tool_calls_withheld` as Publisher series, with matching `SERIES_COUNT` and exposition test updates.
5. Merge: `suraj-revamp` has not been fast-forwarded. Untracked `.agents/tasks/gw34/review.md` and `review.json` are not committed here.
6. The full suite (~10 min) was last run at `d38cadcc`; after the rebase only the quick check above was re-run (the rebase was a no-op).
