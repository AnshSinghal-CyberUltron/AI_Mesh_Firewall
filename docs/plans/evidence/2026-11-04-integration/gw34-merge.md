# GW34 SDK-compat merge into integration-gw27-35
- Merge commit: b6b7844c (git merge --no-ff gw34-sdk-compat onto GW33 note c73e9397; MERGE_HEAD 1f9abe64)
- Fix commits (separate from the merge):
  - 52385590 fix: GW33 canary keeps GW34 template facts when it adds its system slot
  - 5b8c0831 test: align GW34 and GW29 tests with the merged streaming path
  - 7b0a996c fix: perf e2e drivers read the gateway_v2 `: amf.trace` comment trace (the amf.trace consumer fix)
- Handoff: `HANDOFF.md` (GW34 branch root). Live Docker/staging L34-1 / L34-2 NOT run (not claimed here either). V13 resolved by the owner on 4 Nov 2026: trace = SSE comment `: amf.trace {json}`.
- The merge was interrupted once by a host resize. It was continued from the in-progress MERGE_HEAD state, not aborted.

## Conflicts + resolutions (11 files, plus 3 companions in the merge commit)
- edge/routes.py (12 hunks): GW34 moved streaming into `ChatStream` (`edge/chat_stream.py`: deferred-start `StreamResponse`, `ToolCallAssembler`, disconnect watcher). Kept GW29 `first_attempt_refusal` before the stream and `render_incident_refusal` for `INCIDENT_HTTP_CODES`. Kept GW34 plain-HTTP refusals (`del req`, R7) and `run_until_disconnect`. Audit = GW29 `refused_code`, overridden to `STREAM_KILLED` on a client disconnect (GW34 R6), plus GW34 `_tool_call_detail`. Uses GW29 `route_emit.emit_error_code`/`ProducedModeHolder`. GW31 `rag` routes kept; GW34's re-added `_placeholder` routes dropped. The pre-byte `emit_error` passes `holds.incident_retry_after_s`, so a pre-byte incident cut still carries GW29's `Retry-After` and `x-should-retry: false`.
- edge/chat_stream.py (companion, no textual conflict): ported into `ChatStream`: GW29 per-stream control source (`control_factory.for_stream(binding)`, `RefusingControlSource` when unbound), `attempt_recheck_cut` above `mark_dispatched(routed_model)` and `note_stream_cut` at the one terminal site; GW28 `open_chat_upstream` plus routed first-event budget; GW33 `bounded_upstream` and `build_output_detectors` (canary delta, final detector, sync runner). A size-gate breach (`stream` 141 > 120 lines) was fixed by extracting `_controls()`; the limit was not raised. `_ProducedModeHolder` is now an alias of the one `route_emit.ProducedModeHolder`.
- edge/upstream_body.py: GW34 `_render` (tool fields, `render_message_extras`, `FillRefused`) now takes GW29's routed `model` and GW27's `org_ceiling` budget.
- edge/openai_chat.py: GW29/27 `with_openai_body(..., model=, org_ceiling=)` plus GW34's `FillRefused` refusal.
- edge/openai_nonstream.py: GW34 `_validate_output` inside the try, and GW29 `IncidentRecheckRefused` branch.
- edge/openai_responses.py: `_stream_response(..., outcome=outcome, plain_pre_byte=False)`.
- edge/app.py, edge/request_holds.py, egress/strict.py, tests/edge/test_lgw15_input_block.py: union of both sides. In strict.py, GW34 meta channels plus GW33 `RewriteVerificationFailure`/`OutputLengthExceeded` terminations.
- egress/stream.py + stream_meta.py + stream_publish.py (companions): GW34's split kept; GW33's `delta_hits` moved into `stream_meta._Text`. There is ONE `TradeOffUndefined` class (stream_publish re-exports stream_meta's), so `except` matching stays exact.
- runtime/resources.py: all eight mixins. GW34's PROVIDING mixins (`BudgetBoundsMixin`, `StreamBoundsMixin`) go first in the MRO. GW33 `OutputBoundsMixin` and GW28 `RoutingBoundsMixin` re-declare `max_output_tokens` / stream timeouts as host-supplied stubs, and with GW34's order listed last the stub won: `NotImplementedError` at import. A check confirmed no `NotImplementedError` stub now resolves on `ResourceContract`.

## Fix commits
- 52385590: `canary_inject.with_canary` rebuilt `UpstreamTemplate` from messages/stop/slot_count only. That dropped GW34's tools/forwarded/pair_parts/joined and left `input_forwarded` short, so `prompt_chars` raised IndexError on every canary request. Now `dataclasses.replace` runs in a helper `_with_system_slot`, which also clears a dispatch-provenance false positive from an assigned `replace()`. Per-pair tuples grow by one forwarded plain pair. +1 test (canary + tools keeps tool fields upstream).
- 5b8c0831: test signature updates (`_build_pipeline(plan, holds)`, `with_openai_body(model=, org_ceiling=)`, attempt site `ChatStream._next_attempt`). Two GW29 streaming tests now assert the merged behaviour. (a) A recheck refusal at the streaming attempt is pre-byte, so it is HTTP 503 `stream_killed` + Retry-After + `x-should-retry: false` (was a 200 SSE head + error frame; GW29 AC8 says a refused stream is never a 200 SSE head). (b) A non-incident streaming refusal is plain HTTP with its own status and no retry hints (GW34 R7).
- 7b0a996c (amf.trace consumer audit): `git grep` over edge/, gateway_v2/, frontend/, scripts/, tests/ found no consumer of the old named `event: amf.trace` frame. `frontend/src/utils/liveGateway.js` (GW34), `edge/wire/responses_events.py`, `edge/wire/sse_decode.py` and all tests already read `: amf.trace {json}`. The only other stream-trace readers, `scripts/perf/e2e/drive.py` and `load.py`, read `data:` frames only (v1 framing). They could never read a v2 trace under either framing, and now accept the comment line too (v1 path unchanged; smoke-tested with v1 and v2 bodies against a local SSE server).

## Gates (from gateway_v2/, venv /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv, Python 3.12)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 4726 passed, 117 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| pytest serial, docker-on (before the provenance fix) | 2 failed (tenant_scale known + dispatch_provenance, fixed in 52385590), 4725 passed, 5 errors = tests/edge/test_edge_live_l16b.py (live edge container could not reach :8300 after the host restart; skipped under AMF_EDGE_DOCKER=0 as in the GW33 gate) |
| L34-1 SDK matrix (serial, after `npm ci` in tests/openai_conformance/node) | python-sync 10/10 cells, python-async 10/10, node 10/10 (cell c = trace comment passes); 55 passed |
| frontend `node --test` (src/utils, components, constants) | 190 passed, 0 failed |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict gateway_v2 | Success, 340 files |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance / single_authority / console_inventory / credential_flow | all exit 0 |
| check_secret_headers --repo .. | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140 (gateway_v2), state_control/valkey.py:100 |

Not run: live Docker/staging L34-1/L34-2, control Django tests (same reason as GW33). GW34 handoff follow-up V9 (publish tool-call counters as Publisher series) is still open.
