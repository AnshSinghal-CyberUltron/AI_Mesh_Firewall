# GW34 V13: `amf.trace` as an SSE comment (exit note section)

Companion to `exit-note.md` (open finding 1). Branch `gw34-sdk-compat`, not pushed.
Full gate record: `.agents/tasks/gw34/trace-comment-gate.md`. Approved review: `.agents/tasks/gw34/trace-comment-review.md`.

## What changed

Owner decision, 4 Nov 2026: the streaming `amf.trace` frame is no longer a named event
(`event: amf.trace` + `data:`). It is now one SSE comment line, `: amf.trace {compact ascii json}\n\n`,
with the same payload, after the finish chunk and before `[DONE]`. Every compliant SSE parser skips
comments, so the Node `openai` `stream()` helper no longer throws and the Python SDK no longer yields a
choice-less chunk (R1.6). `x-request-id` is unchanged. Code: commits e108d308 and 637852f2.

## Consumers

Found and changed:

* Encoder: `gateway_v2/edge/wire/sse.py` (`SSEEncoder.trace`, `TRACE_COMMENT_PREFIX`).
* Decoder: `gateway_v2/edge/wire/sse_decode.py` (`SSEDecoder.trace`, `trace_comment_payload`); an undecodable trace comment sets `malformed()`.
* `/v1/responses` translator: `gateway_v2/edge/wire/responses_events.py` (`_trace_comment`); malformed trace fails closed.
* Frontend: `frontend/src/utils/liveGateway.js` `consumeSSEStream` (also used by `gatewayChatFetch.js` and the simulators); legacy named-event path still works.
* Tests: `test_stream_output_trace.py`, `test_v2_leaks_enforcement_preservation.py`, `test_lgw12_asgi_conformance.py`, `test_py_sdk_matrix.py`, `test_node_sdk_matrix.py`, `sdk_matrix/node/gw34_matrix.mjs`, `frontend/src/utils/liveGateway.test.js`, `openai_conformance/wire_contract.py` (comment only), new `tests/edge/test_gw34_trace_comment.py`.

No change needed: emitters `edge/chat_stream.py` (`emit_produced_mode`) and `edge/console_projection.py`
(`refusal_trace_frame`), both of which call `encoder.trace`.

Searched, no consumer found: `control/`, `gateway/` (v1; its trace is an unrelated in-band `choices: []`
chunk), `demo/`, `examples/`, `scripts/`, `services/`, `workers/`, `shared/`.

Unverifiable / outside this repo: any external client (customer code, other repos) that parses the old
named event would break. None is known; it cannot be checked from here.

Stale prose, not fixed (non-blocking, from review): `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md:609`
and `docs/plans/2026-10-26-gw14b-console-contract.md:206` still describe the console reading the `data:` line
of `event: amf.trace`. They need a "superseded by GW34 V13" note.

## Gate results (verbatim from `trace-comment-gate.md`; offline only)

```
----------------- L34-1 matrix (ran/passed per client x cell) ------------------
python-sync   a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
python-async  a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
node          a:1/1 b:1/1 c:1/1 d:1/1 e:1/1 f:1/1 g:1/1 h:1/1 i:1/1 j:1/1
=========================== short test summary info ============================
```

```
$ node --test src/utils/liveGateway.test.js
# tests 28  # pass 28  # fail 0
```

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
`dispatch/vector_transports/pinecone_transport.py:140`, identical to the baseline in `verification.md`.

## What remains for the human

* **Live Docker/staging gate: NOT RUN and not claimed.** Boot a real v2 stack and re-run the SDK matrix
  (Python sync/async, Node) plus the console against it, to confirm the comment framing survives real
  proxies and transports. The matrices above are host loopback only.
* Confirm no external client parses the old `event: amf.trace` frame.
* Real browser console check against the new framing (only covered by `liveGateway` unit tests so far).
* Add the "superseded by GW34 V13" notes to the two historical docs named above.
