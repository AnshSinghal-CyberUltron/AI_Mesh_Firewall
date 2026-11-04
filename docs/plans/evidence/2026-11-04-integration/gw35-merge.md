# GW35 console-truth merge into integration-gw27-35 (final merge)
- Merge commit: 057fc825 (`git merge --no-ff gw35-console-truth` onto the GW34 note 78368326; gw35 tip 3c1a3b2d)
- Fix commits (separate from the merge):
  - db67a34a fix: GW35 tests read the GW34 `: amf.trace` comment trace and pre-byte plain HTTP (the amf.trace consumer fix)
  - fb0b5473 test: GW35 kill-code classification scans `edge/chat_stream.py` (GW34 moved the attempt)
  - c67581be test: GW35 console vocabulary pin includes GW33's `rewrite` disposition
  - 952b73a7 test: frontend GW35 vocabulary pin includes GW33's `rewrite`
- Handoff: the gw35 branch has no HANDOFF.md / final-report.md. I read `.agents/tasks/gw35/gates-report.md`, `review.md` and `docs/plans/evidence/2026-11-04-gw35/SUMMARY.md` instead. Live Docker / browser checks were NOT run (not claimed here either).

## Conflicts + resolutions (13 files, plus companions in the merge commit)
- audit/console_schema.py: GW35's `CONSOLE_ACTIONS` / `ACTION_SEVERITY` / fingerprint regexes are kept, and GW33's `rewrite` is added to the dispositions (severity 2, same as redact). Without it a GW33 rewrite output decision fails validation and the console is dropped. `STAGE_ACTION_VOCAB` and the frontend literal both gained `rewrite`; the frontend already labels it "rewritten" and ranks it with redact. Companion: the deciding_rule check now allows a rule on an `allow`/`skip` `model_routing` stage, because GW28 always stamps its `reason|selected|attempt|remapped_from|alias` there.
- edge/console_projection.py: GW35's `ChatFacts` → `console_stages.stages_to_console` replaces GW28's inline `chat_console(...)`. GW28's routing trace is carried as two new `ChatFacts` fields, `routing_rule` (GW28 `deciding_rule(last, alias)`) and `routing_refused` (last decision has a `refusal_code`, so the stage is `skip`/not-ran, as GW28 drew it). `console_from_holds(holds, refuse_code)` / `refusal_console` (GW35) is the one entry point.
- edge/console_stages.py (companion): `rewrite` is in `_DISPOSITIONS`; `model_routing` is drawn with the routing rule.
- edge/openai_nonstream.py: GW35's `note_failure` / `note_output_action` plus `console_from_holds(holds, code)`, keeping GW29's `retry_after_s=enforced.retry_after_s`. GW28's `_console_for` is dropped (superseded). GW34 `META_CHANNELS`/`Disposition`/`open_chat_upstream` imports are kept.
- edge/openai_responses.py: GW34/29 `refused_code` tracking plus GW35's `gate=unauth_audit`, the minted refusal request id, and `refusal_console(holds, code)` alongside GW29's `retry_after_s`.
- edge/request_holds.py: union of the slots and methods (GW27 controls/org tokens, GW28 routing, GW29 incident/reroute, GW33 canary/extras, GW34 tool-call counts, GW35 identity/plan/failure/output-action/fingerprint). I dropped a duplicated `_output_decision` slot. `_DISPOSITIONS` includes `rewrite`.
- edge/audit_emit.py: GW33 `output_extras` / review-queue policy, plus GW35's failure-code / policy-cut recording in `note_stream_output`. `emit_chat_audit` keeps GW29 `feed_attempt`, then GW35 `_audit_org` (unauthenticated sentinel + gate + key fingerprint + chat console), then GW27 `_stamp` + `_suppressed`. `_records` / `_phase` / `_admission` / `_output_failure` take both `org` (GW35) and `stamp` (GW27).
- edge/publisher.py: GW29 `incident` and GW35 `unauth` are both kept. `SERIES_COUNT = series_count(contract, unauth terms) + incident terms`.
- edge/app.py, app_defaults.py, dev_profile.py, openai_chat.py, egress/stream.py, routes.py: union of the imports and parameters (GW28 routing, GW29 control_factory/incident gates, GW34 tool_bounds/wall clock, GW35 unauth_audit). In routes.py, GW34's `ChatStream` / `finish_stream` structure wins: GW35's copy of the old inline `_stream` / `_next_attempt` / `_build_pipeline` / `_finish_stream` bodies was dropped. GW35's behaviour was ported instead: `holds.note_request(minted)` on identity refusal, `holds.note_failure(verdict.code)` on a coalescer refusal, and `refusal_console` in the pre-byte `emit_error` (replacing the old `console_from_holds(output_action=, provider_called=)` keyword API, which no longer exists).
- edge/chat_stream.py, route_emit.py, incident_render.py, incident_recheck.py (companions): `finish_stream` uses GW35's `record_cut(produced, reason)` and `emit_produced_mode` uses `produced.has_trace()`. That replaces GW34's inline `STREAM_KILLED`/`allow→block` patch, so an operational cut becomes `model_output=error`, never an output block. There is ONE `ProducedModeHolder` (GW35's `edge/produced_holder.py`); `route_emit` re-exports it. The GW29 incident refusal/recheck call sites call `refusal_console(holds, code)`.
- Size gate: three breaches came up (`build_app` 121 > 120 lines, `openai_chat.py` 804 > 800, `publisher.py` 819 > 800). I fixed them by moving the admission-family adapter into a new `edge/publisher_admit.py` and condensing comments. No limit was raised.

## Fix commits
- db67a34a (amf.trace consumer check). GW35 product code emits the trace only through `SSEEncoder.trace()`, which is GW34's comment line `: amf.trace {json}`. `frontend/src/utils/liveGateway.js` (`/^:\s?amf\.trace (.*)$/`, GW34) is what feeds `terminalTracePayload` to GW35's `normalizeStreamChatPipelineResult` / `isGatewayV2Trace`, so the console UI already consumes the new framing. A `git grep` found two GW35 consumers of the OLD named frame, both in tests: `tests/edge/_gw35_harness.py::trace_frame` (`event: amf.trace\ndata: `) and `tests/edge/test_gw35_stream_cuts.py::_finish`. Both now read the comment line. Two GW35 tests expected a streamed trace frame for a failure or refusal before the first content byte. Under GW34 R7 that response is plain HTTP with the same console in the JSON body, so they read the body. Two "every audit row matches the frame" checks now skip GW29 `incident_cut`/`incident_refusal` rows, which carry no console by design. All other rows must still match.
- fb0b5473: `test_every_code_routes_kills_is_classified` looked for `kill_latch.kill(<CONST>)` in `routes.py`, but GW34 moved the attempt to `chat_stream.py`. The classification rule is unchanged.
- c67581be / 952b73a7: the GW35 Python lint and frontend vocabulary pins now include `rewrite` (see console_schema above).

## Gates after the merge + fixes (from gateway_v2/, venv /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv, Python 3.12)
First post-merge run: 27 failed. The causes were the amf.trace test consumers, the GW28 routing rule, the kill-site path, the vocabulary pins and the size gate. All were fixed as above.

## Final gate numbers (whole stack GW27+28+29+31+32+33+34+35, HEAD 952b73a7)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 4880 passed, 108 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known GW30 pinecone_transport.py:140) |
| pytest serial, docker-on | 4880 passed, 103 skipped, 1 failed (same known), 5 errors = tests/edge/test_edge_live_l16b.py (live edge container cannot reach :8300, the same environment condition as the GW34 gate; skipped under AMF_EDGE_DOCKER=0) |
| L34-1 SDK matrix (tests/openai_conformance/sdk_matrix) | 55 passed; python-sync / python-async / node all cells pass (cell c = trace comment) |
| frontend `npm run test:unit` (node --test utils/components/constants) | 208 passed, 0 failed |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict gateway_v2 | Success, 346 files |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance / single_authority / console_inventory / credential_flow | all exit 0 |
| check_secret_headers --repo .. | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140 (gateway_v2, GW30), state_control/valkey.py:100 (R2-05) |

Not run: frontend `npm run build` (vite is not installed in this worktree's node_modules), control Django tests, and live Docker/staging/browser checks for GW35.
Open follow-ups:
- Should GW29 incident audit rows carry the console object? Today they do not; Scan Detail reads the input/output rows.
- For a streamed upstream failure, the console shows `output_guardrail allow/ran` (from the synthetic allow on the stream stats). The gw35 branch alone does the same.
- The GW34 follow-up V9 is still open.
