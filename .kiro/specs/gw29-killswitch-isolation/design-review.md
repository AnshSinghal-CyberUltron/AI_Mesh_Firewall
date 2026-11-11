# Design review: GW29 kill switch, isolation, reroute, auto-isolation (revision 6)

Reviewed `design.md` (content is revision 6) against `requirements.md`, the runbook card text and the code in `.worktrees/gw29`.
The revision-5 findings (headers, streaming refusals as HTTP, single audit record, cut record, control typing) are addressed in 6.4a.
The findings below are new. They come from reading the routes that share the chat lifecycle, `_proceed`, and `with_openai_body`.

Verdict: CHANGES_REQUESTED (1 HIGH, 2 MEDIUM, 2 LOW, 0 NIT).

## Findings

1. HIGH. `/v1/responses` reuses the chat lifecycle and the chat streaming/non-streaming helpers, and the design's
   signature changes break or mis-wire it. `edge/openai_responses.py:135` calls `ChatSurface.run_lifecycle` (so phase 4b
   applies), then calls `self.chat._stream_response(forward, plan, destination, upstream, holds)` (line 323) and
   `ChatSurface.collect_and_render(req, plan, destination, upstream, capture.send, holds)` (line ~260) directly, without a
   `LifecycleOutcome`. Section 6.3 makes `_stream_response(..., outcome)` carry the binding and routed model, and gives
   `collect_and_render` new `org_id`/`routed_model` arguments. With the stated defaults, a Responses stream gets
   `control_factory.for_stream(None)` -> `RefusingControlSource` and is cut with `STREAM_SNAPSHOT_STALE` on the first chunk,
   and a non-streaming Responses call gets `fail_closed_recheck` and is refused every time. Alternatively an implementer
   makes the argument optional and the Responses path silently has no in-flight kill. In addition the Responses `_stream`
   sends the SSE head (`start_sse`) before `_stream_response`, so the "first-attempt recheck in `handle()` returns a 503 JSON"
   rule (6.4) cannot hold there, and `ChatRoute.handle`'s `note_incident_cause`/header/audit logic (6.4a) is not on that path.
   The design never mentions the endpoint.
   Fix: add a 6.3a "Callers of the shared helpers". Make `outcome` a required parameter of `_stream_response` and
   `collect_and_render`, update `openai_responses.py` to pass it, and state for Responses: (a) its refusal path must call the
   same incident-aware renderer (HTTP 503 + `Retry-After` + `x-should-retry: false`, one audit record via
   `note_incident_cause`); (b) its first-attempt recheck runs before `start_sse`; (c) a test per endpoint
   (`/v1/chat/completions`, `/v1/completions`, `/v1/responses`, stream and non-stream) proving kill refusal and mid-stream cut.
   Add a grep-based task: every caller of `_stream_response`, `collect_and_render`, `run_lifecycle`.
2. MEDIUM. Endpoints with their own admission path are not covered and not declared out of scope. R1.1 says "The admission
   path SHALL consult the per-process `KillSwitchSnapshot`". `EmbeddingsSurface.handle` (`edge/openai_misc.py:304`) has its
   own `_admit`/`_budget`/`mark_dispatched` and `routes.py:789-791` mounts `/v1/embeddings` and `/v1/moderations`;
   `edge/mcp.py:244` also dispatches. None of them goes through `run_lifecycle`, so a global or org kill leaves them
   serving, and an embedding model kill or isolation is never consulted. The design (grep: no mention of embeddings, misc or
   mcp) neither wires them nor records the exclusion.
   Fix: either (a) call `IncidentGate.decide` in `EmbeddingsSurface.handle` before `_admit` (and global/org checks in
   `ModerationsSurface`/MCP), with the same refusal rendering, tests and `fail_closed_incident` default on `MiscRoute`, or
   (b) add a decision to section 12 ("kill switch covers chat, completions and responses only; embeddings, moderations and
   MCP are a declared gap, owner Gxx") and list it under "Declared OPEN" in section 14 and in section 11. Option (a) is
   preferred because a kill switch that leaves one authenticated endpoint open is a fail-open.
3. MEDIUM. 6.1/6.2 describe `_proceed` and `with_openai_body` in ways the code does not support. `_proceed(self, plan, req,
   decision, request_id)` (`edge/openai_chat.py:589`) has no `identity` or verdict parameter, yet 6.1 builds the `StreamBinding`
   "in `_proceed` from `identity.org_id`, ... `identity.principal.feed_seq`" and calls `holds.note_reroute(...)` there.
   `with_openai_body` has no "model slot": it builds `body = {"model": req.model, ...}` (`edge/upstream_body.py:72`), so
   "rewrite the model slot ... a slot mismatch returns `None`" is wrong, and a mismatch path cannot exist. `req.model` is also
   used for `GatewayRequest(model=...)` (line 625) and the request-id fingerprint (line 654, computed before phase 4b), so an
   override in one place leaves the others on the client string.
   Fix: state the new signature, e.g. `_proceed(self, plan, req, decision, request_id, *, identity, verdict)`, and replace
   the slot wording with: `with_openai_body(..., *, model: str)` uses `model` for `body["model"]`; `_proceed` passes
   `verdict.routed_model` to both `GatewayRequest` and `with_openai_body`. Say explicitly that the request-id fingerprint keeps
   the client string (or the canonical one) and why it is safe. Add a test that the model in the provider-open body equals the
   canonical routed name for a case-mutated request.
4. LOW. The document header is stale and contradicts the body: "Status: REVISION 2 (resolves ... revision-1 verdict ... 7
   MEDIUM, 4 NIT)" while sections 15.000 to 15.1 record six rounds, and the section 15 heading numbers (`15.000`, `15.00`,
   `15.0`, `15.0b`, `15.0a`) are out of order. An implementer cannot tell which revision is authoritative.
   Fix: set the status line to "REVISION 7 (responses to design-review.md on revision 6)", and renumber 15.x
   newest-first (15.6 down to 15.1).
5. LOW. Isolation `origin` default is ambiguous. 10.3 says `origin` defaults to `operator` "only if `no_expiry` explicitly
   true else malformed", while 4.4 only specifies the missing-`expires_at` operator case. An operator record with finite
   `expires_at` and no `origin` field has no stated outcome.
   Fix: one rule: missing `origin` is `operator`; then 4.4's operator expiry rules apply unchanged. Add the case to the
   parser matrix test.

## Verified assumptions

- `KillSwitchSnapshot.state()`, `scope_is_on`, `_set_scope`, `_absorb_locked`, `view()` exist as described; the drift check
  raises before `_refreshed_at` is set, so drift leaves the snapshot OK until `FRESH_MS` (C3 is a real defect).
- `KillSwitchSnapshot` has exactly one construction site, `edge/state_runtime.py:183`.
- `edge/app.py:386` defaults to `_NoControlSource()` (class at `:769`); `InFlightControl.evaluate` orders stale, duration,
  kill, revoke, plan as the design says, and compares `snapshot.age_s` to `max_snapshot_age_s()`, so the C2 `age_s=0.0` rule is
  needed.
- `routes.py` has `_emit_error_code` (175), `_render_refusal` (366), `_next_attempt` (635) and `mark_dispatched` at 689;
  `openai_nonstream.py:198` has `mark_dispatched`; `errors.render_http` has no retry-after parameter (so 6.4a's edit is
  needed); `emit_chat_audit` takes `refuse_code` (audit_emit.py:57).
- `Principal.feed_seq` exists (`admit/identity.py:56,76`).

## Unverified / wrong assumptions

- Wrong: `/v1/responses` is not mentioned and breaks under the 6.3 signature changes (finding 1).
- Wrong: `with_openai_body` has a model slot that can mismatch (finding 3).
- Wrong/unstated: `_proceed` has access to `identity` (finding 3).
- Unverified (not read): the lifespan hook that hosts the pump and ticker; `identity.principal.feed_seq` access path from
  `run_lifecycle`; `RequestHolds.__slots__` accommodating three new fields. The design already lists the first as a verify-first task.
- Unverified (cannot run offline here): live L29-1..L29-4, the benchmark numbers, and Postgres 16 projector tests.
