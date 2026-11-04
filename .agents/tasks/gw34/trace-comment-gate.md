# GW34 V13: `amf.trace` as an SSE comment: gate record

Owner decision 4 Nov 2026. Offline gates only; the live Docker/staging gate was NOT run and is not claimed.
Environment: host venv `/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin` (as in `verification.md`),
run from `gateway_v2/` of the gw34 worktree; node v22 with the committed `openai` 4.104.0 lockfile install.

## Wire change

Before: `event: amf.trace\ndata: {json}\n\n`. After: `: amf.trace {compact ascii json}\n\n` (one line, same
payload, after the finish chunk and before `[DONE]`; `x-request-id` unchanged).

## Consumers of the old `event: amf.trace` frame (found and changed)

| consumer | file | change |
|---|---|---|
| encoder | `gateway_v2/edge/wire/sse.py` `SSEEncoder.trace`, `TRACE_COMMENT_PREFIX` | emits the comment line |
| emitters (no change needed) | `edge/chat_stream.py` `emit_produced_mode`, `edge/console_projection.py` `refusal_trace_frame` | both call `encoder.trace` |
| decoder | `edge/wire/sse_decode.py` `SSEDecoder.trace`, `trace_comment_payload` | trace comment exposed on `.trace`, yields no frame; undecodable trace comment sets `malformed()` |
| /v1/responses translator | `edge/wire/responses_events.py` | reads the comment (`_trace_comment`), folds it into the terminal object; malformed trace fails closed |
| frontend | `frontend/src/utils/liveGateway.js` `consumeSSEStream` (also used by `gatewayChatFetch.js`, the simulators) | parses the comment into `terminalTracePayload`; legacy named-event data path still works |
| tests | `test_stream_output_trace.py`, `test_v2_leaks_enforcement_preservation.py`, `test_lgw12_asgi_conformance.py`, `test_py_sdk_matrix.py`, `test_node_sdk_matrix.py`, `sdk_matrix/node/gw34_matrix.mjs`, `frontend/src/utils/liveGateway.test.js`, `openai_conformance/wire_contract.py` (comment only), new `tests/edge/test_gw34_trace_comment.py` | updated / added |

Searched and found NO consumer: `control/`, `gateway/` (v1; its `stream_orchestration` trace is an unrelated in-band
`choices: []` chunk, untouched), `demo/`, `examples/`, `scripts/`, `services/`, `workers/`, `shared/`. Not verifiable
from this repo: any external client (customer code, other repos) that parses the old named event; none is known.

## SDK matrices (R1.6 asserted in both: every yielded chunk must carry `choices`)

Node cell `c` is a normal passing test (strict xfail removed). Python `_content_chunks` and Node `contentChunks`
now assert instead of filtering.

```
----------------- L34-1 matrix (ran/passed per client x cell) ------------------
python-sync   a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
python-async  a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
node          a:1/1 b:1/1 c:1/1 d:1/1 e:1/1 f:1/1 g:1/1 h:1/1 i:1/1 j:1/1
=========================== short test summary info ============================
```

## Frontend
```
$ node --test src/utils/liveGateway.test.js
# tests 28  # pass 28  # fail 0
```

## Full offline gate (verbatim summary lines)
```
$ python -m pytest -q
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3329 passed, 104 skipped, 8 warnings in 597.60s (0:09:57)
$ mypy --strict gateway_v2
Success: no issues found in 238 source files
$ ruff check .
All checks passed!
$ lint-imports

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.
$ python -m lint.check_capacity_literals
exit=0
$ python -m lint.check_no_module_mutable
exit=0
$ python -m lint.check_frozen_dataclasses
exit=0
$ python -m lint.check_http_outside_edge_resolve
exit=0
$ python -m lint.check_tenant_scale
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
exit=1
$ python -m lint.check_sizes
exit=0
```

The only failing test is the known pre-existing GW30 Pinecone `check_tenant_scale` finding at
`dispatch/vector_transports/pinecone_transport.py:140` (identical to the baseline in `verification.md`).
Passed count rose 3046 -> 3329 versus that baseline because of the GW34 work committed since, plus the 7 new
trace-comment tests. The new property-style tests run 200 iterations (>= 100).
