# Design — GW35 Console data, admin routes and Scan Detail truth

**Status:** DESIGN, revision round 4 (resolves round-4 `design-review.md` findings 1 to 3; see section 16, which also keeps earlier responses). Implements `requirements.md` in this folder. Local half only: no live Docker/staging run, no browser sign-off, no owner signature is claimed.
**Worktree:** `.worktrees/gw35`, branch `gw35-console-truth`, based on `suraj-revamp`.
**Stack (locked on approval):** Python 3.12 stdlib only for gateway code (`gateway_v2`, raw ASGI, `@dataclass(frozen=True, slots=True)`, seeded `random.Random` property tests, pytest, mypy `--strict`, ruff, import-linter). Control plane: Django/DRF, no import of `gateway_v2`. Frontend: React + Vite, plain-JS helpers tested by `node --test`. No new third-party dependency on any side.

## 1. Overview

Today the console draws two stages it never measured (`auth`, `rate_limit` are literal `allow`), draws nothing at all for refusals that happen before the input scan, paints every upstream failure as `output_guardrail: block`, and writes no audit row for a rejected credential. This card makes the trace a pure function of facts recorded on the per-request `RequestHolds`, adds `error` to the console vocabulary end to end, gives the failed-credential path an audit record under a reserved unattributed org, and reconciles the console pages and the admin routes to what v2 actually produces.

The central design move is to remove the four call-site-specific ways of building a console object (`openai_nonstream._console_for`, `console_from_holds` with caller-supplied `output_action`/`provider_called`, `stream_trace_payload`, and the admission audit record that carries no console at all) and replace them with one builder: `console_stages.build_stages(facts)` over a frozen `ChatFacts` value that `console_from_holds(holds, refuse_code)` derives from `RequestHolds` alone. Every surface (HTTP body, SSE `amf.trace`, audit record, simulator) then shows the same stages because they read the same facts. The requirement's honesty rule (a stage is `ran` because a lifecycle fact says it ran, never because of latency or a neighbour) falls out of the shape: the builder has no latency input.

## 2. Code facts this design relies on (read on this branch)

- `edge/console_projection.py` (179 lines): `chat_console` hard-codes `_stage("auth","allow",True,"")` and `_stage("rate_limit","allow",True,"")`; `console_from_holds` returns `None` when `holds.input_decision is None`; `stream_trace_payload` and `refusal_trace_frame` both go through it.
- `audit/console_schema.py` (162 lines): `_ACTIONS={allow,flag,redact,block}`, `_STAGE_ACTIONS=_ACTIONS|{skip}`; `_validate_stage` checks `name` non-empty only; `plan_version` is required; `enforcement_fields` uses `outcome` as the row action but the control adapter prefers `console.zeroshield.action`.
- `edge/request_holds.py`: slotted ledger. `note_tenant`/`note_request` run only after identity+plan (`openai_chat.run_lifecycle`), `note_input` only after budget. `admitted`/`spent` set private flags with no reader. There is no `plan_version`, no identity-resolved flag, no failure code.
- `edge/audit_emit.emit_chat_audit` returns early on `not holds.request_id or not holds.org_id`; `_admission` builds the admission record with `console=None`; `_records` writes an admission record only when both decisions are `None`.
- `edge/routes.py::_render_refusal` mints a random id for identity refusals (`refusal_request_id`) but never puts it on `holds`; a streamed identity refusal is rendered as plain HTTP (GW36 S3, N11). **`routes.py` is 799 lines; the size gate (`lint/check_sizes`) is 800.** Anything added there must be paid for by moving code out (see 5.9).
- `edge/openai_nonstream._console_for` sets `output_action="block"` for any refusal after dispatch. `collect_and_enforce` turns `UpstreamFailed(code)` into `refuse_code=code`, `OutputBlocked` into `output_blocked` with a decision, and any other raise into `scan_failure`.
- `egress/stream.StreamPipeline._terminate` sets `stats.output_action="block"` for every error except `killed`; `routes.py` ~L601 does the same for a kill-latch reason. Neither records the cause.
- `edge/identity_resolver.CachingIdentityResolver.resolve_outcome` returns `IdentityOutcome.invalid()` (401) for a missing key, junk token, or a verified absence (`IdentityCache` returns `None` for revoked/expired/unknown), and `unverified()` (503) on `StoreDataUnavailable`. **It never knows the org of a revoked key.** `hashed_bearer_key(scope)` returns the sha256 hex only for a 48-char console-key shape.
- `AuditRecord.__post_init__` requires non-empty `org_id`. `audit/sink.py` already uses a reserved underscore org (`CALIBRATION_ORG="_calibration"`).
- Control: `v2_audit_adapter._RANK={allow:0,flag:1,redact:2,block:3}`, `_stricter` ranks unknown as -1; `translate` takes `zeroshield.action` only if it is in `_RANK`; `v2_audit_ingest` already honours `AMF_V2_AUDIT_ORG_MAP` and skips an unmapped org; `policy/v2_shadow.py` partitions v2 rows by `metadata.source`; `security_views.py:~1386` computes `gateway_v2.total_threats = shadow["total"]`.
- Frontend: `pipelineTrace.js` already has `error` in `_STAGE_SEVERITY`/`_STAGE_SCORE`; `StageTimeline.jsx` already prints "Failure reason:" for `error`; `liveGateway.js::inferFinalAction` already returns `error` for upstream-provider errors but `pipeline_trace.final_action` wins first; `data.pipeline_trace.stages.length >= 6` selects the trace path at L1146/L1183; nothing in `frontend/src` reads the trace `source` field today (only `useFirewallData.js` reads `socKpis.gateway_v2`). `npm run lint` is an alias of `npm run test:unit`.
- Inventory: `lint/console_inventory.py` scans the frontend with regexes and writes `docs/plans/evidence/2026-10-26-gw14b/{frontend-verifier/fields.json,cp-consumers/endpoints.json,config-keys/firewallconfig.json}`; `lint/admin_ledger.py` checks `admin-ledger/routes.json` (rows: `path, decision, owner, reason, callers`). The progress file records the inventory test **already failing on a clean HEAD** (`cp-consumers` and `frontend-verifier` drift).

## 3. Decisions (all requirements-level decisions D-01..D-14 stand; these resolve the rest)

- **D-15 One builder, facts only.** The console is `build_stages(ChatFacts)`. `console_from_holds(holds, refuse_code)` is the single entry; the `output_action=` and `provider_called=` parameters are removed, so no call site can contradict the ledger.
- **D-16 Severity order and final action.** `block > error > redact > flag > allow`; `skip` is ignored. `final_action` = highest-severity action among stages. This matches D-01 and makes "final is `error` iff the provider failed and nothing stricter (a `block`) was decided" true by construction. A prior `redact` or `flag` does not hide an upstream `error`.
- **D-17 Failure classification lives in `domain`.** New `domain/failure_class.py` holds `OPERATIONAL_CODES` (a `frozenset`) and `is_operational_failure(code)`. The default for an unknown code is **policy** (`block`), the existing fail-closed behaviour. Operational (shown as `error`): all `upstream_*` codes in `edge/errors.py`, `stream_malformed_upstream`, `stream_inter_chunk_timeout`, `stream_idle_timeout`, `stream_write_timeout`, `stream_max_duration`, `stream_buffer_unavailable`. Policy (stay `block`): `output_blocked`, `policy_regex_timeout`, `stream_key_revoked`, `stream_plan_changed`, `stream_snapshot_stale`, `scan_failure`, `forced_release_tradeoff`, `holdback_overflow`, `undefined_tradeoff`, `strict_buffer_overflow`. **Round 3 additions (all in `domain/failure_class.py`, closed frozensets, one spelling):**
  - `NO_ACTION = frozenset({"killed", posture.STREAM_KILLED})` with `is_no_action(code)`. The kill switch terminates with the literal `"killed"` (`egress/stream.py:793`, `egress/strict.py _KILLED_CODE`); the edge renders it as `stream_killed`. Both spellings publish **no** action and no failure: `note_failure` and `note_output_action` are skipped for them, so the console and `X-ZeroShield-Action` are exactly the pre-change values.
  - `INPUT_SIDE = frozenset({"input_blocked", "input_unmaskable"})`: input-side policy refusals returned after `note_input`. The refusing stage is `input_scan`.
  - `PREDISPATCH_OPERATIONAL = frozenset({"lifecycle_incomplete", "credential_unavailable", "provider_unreachable", "store_not_configured", posture.PLAN_UNAVAILABLE})`: internal or staging faults before the provider opened; shown as `model_routing: error`. `plan_unavailable` is included because `routes.py:682` calls `kill_latch.kill(PLAN_UNAVAILABLE)` in-stream before `mark_dispatched()` (round 4, finding 2); the lifecycle-time `plan_unavailable` refusal has no input decision and reaches rule 4's skip/skip branch, not this one. `OPERATIONAL_CODES` includes this set, so `is_operational_failure` is true for them.
  - `egress` and `edge` both import it (both are above `domain`), so the classification has one spelling. Unknown codes still default to policy (`block`); the coverage test (section 11) is scoped to the codes that can reach the output side and the pre-dispatch tables, not to all of `errors.py`.
- **D-18 A provider raise that is not `UpstreamFailed` stays `scan_failure` → `output_guardrail: block`.** `collect_and_enforce` cannot tell it from a scanner raise. Splitting it needs a change to `collect_and_enforce`'s exception contract and is recorded as a known gap, not fixed here (fail-closed direction kept).
- **D-19 Unattributed records use org `_unauthenticated`.** Because `IdentityCache` cannot name the org of a revoked key (it returns `None`), D-10's "if the resolver knows" branch is never taken in this card: **every** failed-credential record goes to `_unauthenticated`. `IdentityOutcome` gains no org field. A future card that teaches the cache to remember revoked-key owners can change this without touching the schema.
- **D-20 `rejected`/`shed` keep today's rank (-1); only `error` is added.** `v2_audit_adapter._RANK` stays the console vocabulary only: `{allow:0, flag:1, redact:2, error:3, block:4}`; ranking uses `_RANK.get(action, -1)` exactly as now, so a `rejected`/`shed` row (a record with no console) still ranks below every console action and `merge_row` behaves as today for them. Acceptance is a separate constant `_ACCEPTED = frozenset(_RANK) | {"rejected", "shed"}`: a console action outside the six-word vocabulary, or a no-console outcome outside `_ACCEPTED`, is `RejectedV2("action")` (req 3.5, no re-ranking). Producers of no-console `rejected`/`shed` rows, enumerated and pinned by the merge test: `audit.record` admission/shed builders used by `emit_chat_audit` for non-chat routes without a console, and `edge/mcp.py` records whose `ctx.console()` is `None`. Chat admission records now carry a console (5.8), so they are `block`/`error` rows, not `rejected` rows; this is a data change for new rows only, not a ranking change.
- **D-27 Source carrier.** The v2 discriminator on the wire is body `source: "gateway_v2"` (live responses and the `amf.trace` frame) and `metadata.source` (threat-feed events, written by control today). See 5.3 and 9.2.
- **D-28 One code, three readers.** `facts_from_holds` precedence and `note_failure` at every refusal site (5.3).
- **D-29 Pre-dispatch failure after an input decision is a `model_routing` stage outcome** (5.2 rule 4). Option 2 (forbid `allow`) is kept as a validated consequence, not the mechanism.
- **D-30 Plan-unavailable identity.** The lifecycle already has the identity at that point, so `run_lifecycle` calls `holds.note_tenant(identity.org_id)` and `holds.note_request(mint_request_id())` (a minted `zs-` id from the new code-independent `identity_resolver.mint_request_id()`; `refusal_request_id(code)` takes a code and returns `None` for `plan_unavailable`, so it cannot be used here) immediately before returning `_refused(plan_refuse)` / `_refused(REFUSE_PLAN_UNAVAILABLE)`. The `_refused(...)` call itself is untouched, so `x-request-id` and the error body `request_id` stay exactly as today (req 2.6); the minted id appears **only** in body `zeroshield.request_id` and the audit record. `validate_console` requires a non-empty `request_id` (finding 1). The success path still calls `note_tenant`/`note_request` later with its own id; the early calls are on refusal branches that return, so there is no double write.
- **D-31 Route scope.** `_audit_org` applies the `_unauthenticated` sentinel only when `route == "chat"` (the default route, which `openai_responses.py` also uses because it calls `emit_chat_audit` without `route=`). `edge/mcp.py` passes `route="mcp"` and its own console, so it never gets the sentinel; an unauthenticated MCP call stays unaudited and is recorded as a known gap (section 14). `openai_responses.py` L246 becomes `console_from_holds(holds, outcome.refuse_code)` and its `_refuse` calls `holds.note_failure`; `mcp.py` is unchanged apart from keeping its signature-compatible `emit_chat_audit` call. A Responses regression test (failed credential on `/v1/responses` → `_unauthenticated` record, `auth: block`, 401 unchanged) is in section 11.
- **D-21 Threat totals.** `gateway_v2.total_threats` counts rows with action in `{flag, redact, block}` and `metadata.refused_at` not in `{auth, rate_limit}`. `refused_at` is a new additive adapter field (name of the first stage whose action is `block` or `error`, from the trace). Operational refusals (401/429/503) and `error` rows are listed in the feed and counted in a new `gateway_v2.refused_total`/`gateway_v2.error_total`, not in "threats".
- **D-22 Retired routes are keyed by an explicit setting.** `AMF_GATEWAY_BACKEND` (`v1` default, `v2`). With `v1` the proxies behave exactly as today. With `v2` the retired proxies answer 410 `retired_on_gateway_v2` and never contact the gateway. The control table of retired paths is a small constant in control; a gateway-side lint test asserts it equals the `retire` ledger rows that have console callers, so the two cannot drift.
- **D-23 Fingerprint is a record `detail` field, not a console field.** `detail["key_fingerprint"]` = first 12 hex of `hashed_bearer_key(scope)`; omitted when the token is not console-key-shaped (no hash of junk is stored). The console object's frozen key set is untouched.
- **D-24 `plan_version` is optional in the schema.** Required whenever `input_scan.ran` is true; forbidden to be fabricated (an empty string is invalid). Present for a shed/rate-limit/budget refusal (plan already pinned), absent for identity and `plan_unavailable`.
- **D-25 Trace stage ordering rule.** `validate_console` requires stage names to be a subsequence of `STAGE_NAMES` in canonical order with no duplicates (not necessarily all six, to keep existing partial-stage fixtures valid). `chat_console` always emits all six; a test pins that.
- **D-26 Rate bound for unauthenticated records** is a new `ResourceContract.unauth_audit_rate()` (records per `metrics_window_s()`), `workers() * _UNAUTH_AUDIT_RATE_MULT` with the multiplier defined next to the other derivations. The multiplier is an unmeasured starting value flagged in the evidence as an owner decision pending the signed capacity; the method raises `CapacityUnavailable` below 1, and the gate fails closed (drops and counts) when it cannot be derived.

## 4. Architecture and layering

Bottom-up, each layer imports only layers below it (contract `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`):

| Layer | New / changed | Imports |
|---|---|---|
| `domain` | **new** `failure_class.py` | stdlib, `domain.posture` |
| `runtime` | `resources.py`: `unauth_audit_rate()` + its multiplier (the only new capacity literal) | existing |
| `audit` | `console_schema.py` (vocabulary, `STAGE_NAMES`, severity, `final_action`, stricter validation, fingerprint check); `record.py` (`UNAUTHENTICATED_ORG`, `output_failure()` builder) | `domain`, `audit.record` |
| `egress` | `stream.py::_terminate` classifies via `domain.failure_class` (no `edge`, no `detect` import) | `domain` |
| `edge` | **new** `console_stages.py`, `unauth_audit.py`, `produced_holder.py`; changed `console_projection.py`, `request_holds.py`, `audit_emit.py`, `openai_chat.py`, `openai_nonstream.py`, `openai_responses.py`, `routes.py`, `publisher.py`, `series_count.py` | below layers + sibling `edge` |
| control | `core/v2_audit_adapter.py`, `policy/security_views.py`, `core/gateway_admin_proxy_views.py`, `mcp_connector/views.py`, new `core/retired_routes.py` | no `gateway_v2` import |
| frontend | `utils/pipelineTrace.js`, `utils/liveGateway.js`, `utils/retiredRoute.js` (new), simulator/log/feed components | n/a |
| lint | `lint/console_inventory.py`, `lint/admin_ledger.py`, `lint/check_console_inventory.py` | n/a |

`console_projection` stays in `edge`. The stage vocabulary and severity live in `audit.console_schema` (below `edge`) so the validator and the builder cannot disagree; `edge` imports them. `egress` cannot import `edge`, which is why the failure classifier is in `domain`.

## 5. Component design (gateway)

### 5.1 `audit/console_schema.py`

```python
STAGE_NAMES = ("auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail")
STAGE_ACTION_VOCAB = ("allow", "flag", "redact", "block", "error", "skip")   # literal tuple: parsed by the lint
_DISPOSITIONS = frozenset({"allow", "flag", "redact", "block"})
CONSOLE_ACTIONS = _DISPOSITIONS | {"error"}
_STAGE_ACTIONS = CONSOLE_ACTIONS | {"skip"}
ACTION_SEVERITY = MappingProxyType({"allow": 0, "flag": 1, "redact": 2, "error": 3, "block": 4})
def final_action(actions: Iterable[str]) -> str: ...   # max severity, "allow" if none; skip ignored
```
`_ACTIONS` is removed; `zeroshield.action` and `final_action` validate against `CONSOLE_ACTIONS`. `domain.decision.Disposition` is not touched (D-01).

`MCP_STAGE_NAMES = ("auth", "rate_limit", "input_scan", "tool_call", "output_guardrail")` is exported from this module (lowest layer) and `edge/mcp_console.MCP_STAGES` re-uses it. The signature becomes `validate_console(console, *, stage_names: tuple[str, ...] = STAGE_NAMES)`; `mcp_console()` passes `MCP_STAGE_NAMES`. The name-set rule and the D-25 subsequence rule apply to whichever tuple was passed, so MCP consoles (stage `tool_call`) keep validating (review round 2, finding 1). Of the other new rules, `allow => ran`, `skip => not ran`, the non-empty `request_id` and the `error` vocabulary hold for MCP too (`mcp_console._stage` already sets `ran=tool_called` on its `skip`/`allow` pair and mints a request id); the `error and not ran only on input_scan` exception is chat-only and a name check, so it is inert for MCP. The MCP-only tuple is not used by chat code; a chat console containing `tool_call` is rejected.

`_validate_stage` additionally enforces: `name in stage_names`; `action == "skip"` ⇒ `ran is False`; `ran is False` ⇒ `action == "skip"` **or** (`action == "error"` and `name == "input_scan"`) (D-05); `action == "allow"` ⇒ `ran is True` (L14b-2); `deciding_rule`, when present, is a non-empty string and the stage's action is not `allow`/`skip`. `validate_console` requires `zeroshield.request_id` to be a non-empty string (finding 1). `_validate_trace(trace, stage_names)` enforces D-24, D-25 and `final_action == final_action(stage actions)`.

`_reject_evidence` gains `_check_fingerprint(detail)`: `key_fingerprint`, if present, must match `^[0-9a-f]{12}$`; any string value in `detail` or the console that is a 64-hex string raises `SchemaError` (a full credential hash must never be stored). Failures raise `SchemaError`, which the caller treats per 7.

### 5.2 `edge/console_stages.py` (new)

```python
@dataclass(frozen=True, slots=True)
class StageOutcome:
    name: str; action: str; ran: bool; rule: str = ""

@dataclass(frozen=True, slots=True)
class ChatFacts:
    refuse_code: str            # "" when the request was not refused
    identity_resolved: bool
    plan_version: str           # "" = no plan pinned
    admitted: bool; spent: bool
    input_action: str           # "" = no input decision
    input_rule: str
    dispatched: bool
    failure_code: str           # operational or policy cut/refusal code from the output side, "" none
    output_action: str          # "" = no output decision/cut action
    output_rule: str

def build_stages(facts: ChatFacts) -> tuple[StageOutcome, ...]   # pure, total over ChatFacts, always six, canonical order
def stages_to_console(request_id: str, facts: ChatFacts) -> dict[str, object] | None
```
`build_stages` rules, evaluated in order, each from facts only:
1. **auth.** `identity_resolved` → `allow, ran`. Else `refuse_code == invalid_api_key` → `block, ran, rule`; `shared_state_unavailable` → `error, ran, rule`. Else (no identity and not an identity code: parse/pre-check) the builder is never reached (see `stages_to_console`).
2. **rate_limit.** `refuse_code` in `{overload_shed, rate_limited}` → `block, ran`; `budget_unavailable` → `error, ran`; else `spent` true → `allow, ran`; else (identity refusal, or the lifecycle `plan_unavailable` where nothing was spent) → `skip, not ran`. An in-stream `plan_unavailable` kill happens after `spent`, so it reads `allow, ran`. Rule = the code when non-allow.
3. **input_scan.** `input_action` set → `(input_action, ran, input_rule if non-allow)`. `refuse_code == plan_unavailable` → `error, ran=False, rule plan_unavailable` (D-05). `refuse_code == scan_failure` and `spent` → `error, ran=True, rule scan_failure`. Otherwise `skip, not ran`.
0. **Preamble.** If `failure_code` is in `NO_ACTION`, it is treated as unset for rules 4 and 5 (kill-switch cut: stages 4/5 keep their pre-change values and `final_action` is unaffected). In practice `note_failure` is never called for such a code, so this is a defence in depth.
4. **model_routing / model_output.** (4.0) `refuse_code` in `INPUT_SIDE` (after `note_input`): `input_scan` takes `block` (rule = code, `ran`; this overrides the recorded input action, which for `input_unmaskable` is a `redact` that did not complete), `model_routing` and `model_output` are `skip`. `INPUT_SIDE` is checked **before** every other rule-4 branch. `dispatched` false and no code, or code with the input decision `block` → both `skip`. **`dispatched` false, a code set and NO input decision (identity, shed, rate-limit, budget, lifecycle `plan_unavailable`, input `scan_failure`; round 4, finding 3) → both `skip`, `ran=False`.** **`dispatched` false, a code set, and an input action in `{allow, flag, redact}` (D-29, finding 4: `stream_buffer_unavailable`, in-stream `plan_unavailable`/kill before the provider opened) → `model_routing: error` if `is_operational_failure(code)` (this covers `PREDISPATCH_OPERATIONAL`: `lifecycle_incomplete`, `credential_unavailable`, `provider_unreachable`, `store_not_configured`) else `block`, `ran=True` (the dispatch step ran and failed), rule = code; `model_output: skip`.** `dispatched` true → `model_routing: allow, ran`; `model_output`: `error, ran, rule failure_code` iff `failure_code` and `is_operational_failure(failure_code)`, else `allow, ran`.
5. **output_guardrail.** Evaluated in order: (a) an `output_action` set **and in `{allow, flag, redact, block}`** (a resolver decision, or a policy stream cut recorded by `note_output_action`) → `(output_action, ran, output_rule or failure_code)`; `error` never drives `output_guardrail` (round 4, finding 1): `note_output_action` ignores any value outside the four dispositions, so an operational failure cannot paint `output_guardrail: error`; (b) **`dispatched`, `failure_code` set and not in `NO_ACTION`, `is_operational_failure(failure_code)` false and no `output_action` → `block, ran=True, rule failure_code`** (a policy code with no decision, such as `policy_regex_timeout` or any unlisted code, fails closed as an output block; finding 4); (c) otherwise `skip, not ran`. An operational failure therefore stays `skip` (requirement 3.2) unless the guard had already decided on earlier released bytes (a pass-through stream with a recorded ALLOW decision shows `output_guardrail: allow, ran` next to `model_output: error`, `final_action: error`), and the provider failing alone never yields `block`. Rule (b) makes the invariant total: **whenever a code outside `NO_ACTION` is set and a request was refused, `final_action != allow`** (a stage 1-5 rule always fires for it).
The validator pins the consequence: `final_action` may not be `allow` when the console was built with a code (`ChatFacts` carries it; `validate_trace` receives no code, so the builder asserts it and property P2 checks it). A refusing stage at 1–3 forces every later stage to `skip` (requirement 1.4) because the later facts (`spent`, `input_action`, `dispatched`) are false on those paths; a property test pins the equivalence.

`stages_to_console` returns `None` when none of `{identity_resolved, refuse_code in TRACE_CODES, input_action}` holds, where `TRACE_CODES = {invalid_api_key, shared_state_unavailable, plan_unavailable, overload_shed, rate_limited, budget_unavailable, scan_failure}` (constants from `domain.posture`/`edge.chat_lifecycle`). This is the D-06 boundary: parse, pre-check and boundary refusals keep no trace. Unknown codes without an input decision → `None` (fail closed: no trace, refusal unchanged).

### 5.3 `edge/console_projection.py`

`chat_console(*, request_id, facts)` calls `stages_to_console` internals and `validate_console`. `console_from_holds(holds, refuse_code)` = `facts_from_holds(holds, refuse_code)` + builder; wrapped so **any exception returns `None` and logs one WARNING** (`amf.edge.console`, no request content, includes request id and exception class) — requirement 8.3. `apply_projection` copies three keys into the body: `zeroshield`, `pipeline_trace` and the new **`source: "gateway_v2"`** (D-27; the console dict's `source` is already `gateway_v2`, it was just never copied). The `X-ZeroShield-Action` header is set from the final action as today, and now also on pre-scan refusals because those responses carry a console. The golden-diff allow-list in the integration tests is therefore `{zeroshield, pipeline_trace, source}`; requirement 2.6 is read as "no other key, header, status or `x-request-id` change", and this one additive key is the documented conservative extension. The SSE `amf.trace` frame payload carries the same `source`. `stream_trace_payload(produced, stats, degenerated)` reads **only `holds`** (the cut helpers in 5.7 have already copied `cut_reason`/`stats.error` onto it) and calls `console_from_holds`; its signature is otherwise unchanged. `refusal_trace_frame(holds, code)` takes the code. A new `refusal_console(holds, code)` helper (used by `routes._render_refusal`) removes that site's inline lambda. `refusal_console(None, code)` returns `None` (`holds` is optional in `_render_refusal`). Ordering contract: the identity branch calls `holds.note_request(request_id)` **before** `refusal_console`, because `validate_console` needs the request id; a test asserts a non-empty `zeroshield.request_id` equal to the audit record's request id.

`facts_from_holds(holds, refuse_code=None)` is defined exactly (D-28): `code = refuse_code or holds.failure_code`; if `holds.dispatched` the code is `ChatFacts.failure_code` and `ChatFacts.refuse_code` is `""`, otherwise the code is `ChatFacts.refuse_code` and `failure_code` is `""`. All other fields come from `RequestHolds` properties (5.4); stats are never read. Every refusal site calls `holds.note_failure(code)` (write-once) before rendering: `routes._render_refusal` and `openai_responses._refuse` for lifecycle refusals, `emit_enforced_chat` and `note_stream_output` for post-dispatch ones. Because `emit_chat_audit` calls `console_from_holds(holds, refuse_code)` and the response path calls it with the same code (which `note_failure` has already stored), the audit console equals the response console even where the caller passes `None` to audit. A parametrised test asserts audit-console == response-console for every code in `TRACE_CODES` plus the operational and policy stream codes, HTTP and stream; a second test covers each cut reason (inter-chunk timeout, key revoked, malformed upstream): frame, audit record and `X-ZeroShield-Action` agree and none says `allow`.

### 5.4 `edge/request_holds.py`

Add slots and writers (all O(1), no raise): `_identity_resolved`, `_plan_version`, `_failure_code`, `_output_action`, `_output_rule`, `_key_fingerprint`; methods `note_identity_resolved()`, `note_plan(version)`, `note_failure(code)`, `note_output_action(action, rule="")`, `note_key_fingerprint(fp)`; read properties for all of them plus `is_admitted`, `is_spent` (the existing private flags). `note_failure` is write-once-wins-first so a later `finally` cannot overwrite the first cause. The class stays a plain slotted object (not shared across requests).

### 5.5 Lifecycle recording (`edge/openai_chat.py`)

`edge/identity_resolver.py` gains `mint_request_id() -> str` (`f"zs-{secrets.token_hex(12)}"`) and `refusal_request_id(code)` becomes `mint_request_id() if code in _IDENTITY_REFUSALS else None` (behaviour and its existing tests unchanged). `_render_refusal`'s identity branch and `run_lifecycle`'s plan branch both use `mint_request_id()` through that one definition (review round 2, finding 2). `mint_request_id` is added to `__all__`.

In `run_lifecycle`:
- identity refusal: before returning, `holds.note_key_fingerprint(fp12)` where `fp12 = (hashed_bearer_key(scope) or "")[:12]`. On success `holds.note_identity_resolved()`.
- after the plan resolves: `holds.note_plan(plan.version)`.
- plan refusal (D-30): `holds.note_tenant(identity.org_id)`, `holds.note_request(mint_request_id())`, then `holds.note_failure(REFUSE_PLAN_UNAVAILABLE)` before the existing `_refused(...)` return. Every other lifecycle refusal gets `note_failure(code)` from the renderer (5.3).
- the scan-failure path already has `spent`; nothing new is needed.
`openai_chat.py` is 712 lines; the change is under 14 lines (limit 800).

`routes._render_refusal`: when it mints the identity request id, call `holds.note_request(request_id)` so the audit emit has a key; call `refusal_console(holds, code)` / `refusal_trace_frame(holds, code)`. `routes.py` is 799 of 800 lines today (`wc -l`). Budget: `_ProducedModeHolder` (class + docstring, L212-231 plus two blank lines) is 22 lines removed; the added import is 1 line, the `cut_reason` handling in `_finish_stream` about 6, `_render_refusal` about 4, plus the new `ChatRoute.unauth_audit` field with its docstring line, the `gate=` argument at the `emit_chat_audit` call and the import (about 4 lines), so the planned net is about -7 (≈ 792). `check_sizes` runs in commit 5 and fails the commit above 800. To stay under the gate the same commit moves `_ProducedModeHolder` into new `edge/produced_holder.py` (adding `cut_reason: str | None` slot) and `routes` imports it. A streamed identity refusal remains plain HTTP with the console in the body (2.7).

### 5.6 Non-stream failures (`edge/openai_nonstream.py`)

`emit_enforced_chat` becomes: record `holds.note_failure(enforced.refuse_code)` when set; if `enforced.output_decision` exists `holds.note_output(...)` and `holds.note_output_action(decision.disposition.value, rule)`; for `output_blocked`/`scan_failure` with dispatch and no decision, `note_output_action("block", code)`. Then `console = console_from_holds(holds, enforced.refuse_code)`. `_console_for` is deleted (its `block`-on-any-refusal line is the defect). HTTP status/envelope are produced exactly as before.

### 5.7 Streaming (`egress/stream.py`, `edge/routes.py`)

**Single rule (round 4, finding 1): `holds.output_action` carries output-guard dispositions only (`allow`, `flag`, `redact`, `block`). An operational failure is carried by `failure_code` alone and never calls `note_output_action`.** `_terminate` in `StreamPipeline` sets `stats.output_action = "error" if is_operational_failure(error) else "block"` for `not is_no_action(error)` and `output_action is None` (a stats-only value; `stats.error` carries the code). In `routes._finish_stream` the kill-latch branch: for a reason that is not `STREAM_KILLED`, set `produced.cut_reason = reason` (and the same stats value). `note_stream_output(holds, stats, version)` additionally, when `stats.error` is set, `not is_no_action(stats.error)` and there is no `Decision`, calls `holds.note_failure(stats.error)` and, **only if `not is_operational_failure(stats.error)` and `stats.output_action == "block"`**, `holds.note_output_action("block", stats.error)`; it never forwards `"error"`. Signature: `note_output_action(action: str, rule: str = "") -> None`; an empty, `None` or non-disposition action (including `"error"`) raises nothing and is ignored (no write), so a caller cannot blank a recorded action or smuggle `error` into the guard stage.
**STRICT pipeline (review round 3, finding 3).** `StrictWithholdPipeline._terminate` (`egress/strict.py:414`) sets only `incomplete` and `error`; `StrictStats.output_action` stays `None` on every abnormal exit. This card does **not** edit `strict.py`. The strict path relies on `failure_code` alone: `note_stream_output` records `note_failure(stats.error)` and skips `note_output_action` (action `None`). Rule 5 then gives: policy code (`strict_buffer_overflow`) → 5(b) `output_guardrail: block, ran`; operational code (`stream_malformed_upstream`, `stream_inter_chunk_timeout`, other `stream_*` timeouts) → `model_output: error`, `output_guardrail: skip`; kill (`killed`) → `NO_ACTION`, nothing recorded. The two pipelines yield identical consoles for identical causes when the guard activity is the same; the one legitimate difference is that a pass-through stream that had already recorded its synthetic ALLOW and then fails operationally shows `output_guardrail: allow, ran` (the guard ran on earlier bytes), while the strict pipeline, which withholds, shows `skip`. Both show `model_output: error` and `final_action: error`. **Ordering fix (review round 2, finding 3):** `note_stream_output` runs inside `_attempt` right after `pipeline.run`, before the kill-latch branch of `_finish_stream`, and it writes a synthetic ALLOW `Decision` for a pass-through cut. The kill-latch branch therefore also writes to `holds`, through one helper `produced_holder.record_cut(holds, produced, stats, reason)` (in the new module, to stay under the size gate): it sets `produced.cut_reason`, `stats.output_action`, calls `holds.note_failure(reason)` and, **only when `not is_operational_failure(reason)`**, `holds.note_output_action("block", reason)`; everything is skipped when `is_no_action(reason)` (kill switch: nothing is written, so frame, audit and header are unchanged). `note_output_action` is **last-write-wins** and a policy `block` overrides the synthetic allow (an operational failure does not touch it); `facts_from_holds` reads `holds.output_action` first and only falls back to the stored `Decision` disposition when no explicit output action was noted. The audit output record is built from the same `holds` facts (explicit output action beats the stored Decision's disposition), so frame, audit record and `X-ZeroShield-Action` agree. `note_failure` stays write-once-first: the cause is the first recorded code, while the output action is the final truth. A client disconnect with no error sets neither (3.3). `NO_ACTION` codes (`killed`, `stream_killed`) publish nothing, on both pipelines.

### 5.8 Audit (`edge/audit_emit.py`, `audit/record.py`, `edge/unauth_audit.py`)

`emit_chat_audit(emit, holds, refuse_code, ..., gate=None)`:
1. `observe_outcome` as today.
2. `org = _audit_org(holds, refuse_code)`: `holds.org_id`, or `UNAUTHENTICATED_ORG` iff `refuse_code in {invalid_api_key, shared_state_unavailable}` and `holds.org_id` is empty (a named seam so the negative control can restore the legacy early return). If `emit is None`, `not holds.request_id` or no org → return.
3. For `UNAUTHENTICATED_ORG`, `gate.admit()` must return `True`; a `False` increments the gate's drop counter and returns. A missing gate drops (no unbounded path).
4. `console = console_from_holds(holds, refuse_code)` for every chat record, **including admission records**: `_admission` now passes the console to `_annotate`, so the admission row action is `block`/`error`, not `rejected`.
5. `_records`: input and output records as today. The admission branch condition changes from "both decisions None" to "no input decision and no output decision, refused". A new branch: when `holds.dispatched`, no output `Decision`, and `holds.failure_code`, append `output_failure(...)`, an `OUTPUT`-phase record with `outcome="rejected"` and `detail={"reason": code, "failure_class": "operational"|"policy", "status": http_status}` (D-08, no schema bump). `status` comes from `errors.render_http(code).status` guarded like `_admission`.
6. `detail["key_fingerprint"]` is added for the unauthenticated records only.
The function keeps its try/except-all body; a broken sink callback or a console failure cannot change the already-sent response (4.7).

`edge/unauth_audit.py`:
```python
class UnauthAuditGate:             # plain class with __slots__ (mutable by design), one per process, held on ChatRoute, never module-level
    __slots__ = ("_rate", "_window_s", "_clock", "_window_start", "_count", "_accepted", "_dropped")
    def __init__(self, *, rate_per_window: int, window_s: float, clock: Callable[[], float])
    def admit(self) -> bool            # fixed-window counter, O(1), never raises
    @property accepted, dropped
class UnauthAuditCounters:             # label-free producer, SERIES_COUNT = 2
    series() -> amf_audit_unauthenticated_total, amf_audit_unauthenticated_dropped_total
```
`build_unauth_gate(contract, clock)` derives `rate_per_window=contract.unauth_audit_rate()`, `window_s=contract.metrics_window_s()`; on `CapacityUnavailable` it returns a gate with rate 0 (drops all, counted) and logs once at ERROR. `ChatRoute` gets `unauth_audit: UnauthAuditGate | None = None`; `dev_profile.py` builds it beside `audit=`; `production_profile.py` keeps `_NoAudit` and `None` (no emit, so nothing to bound). The frozen `ChatRoute` carries a mutable gate; this is the same pattern as `exports: PerRequestExports`, and the gate holds no request data.

`edge/publisher.py` / `edge/series_count.py`: register `UnauthAuditCounters` as a seventh flat producer; `Publisher.SERIES_COUNT` expression gains `UnauthAuditCounters.SERIES_COUNT` (2) and its documented sum and the existing "rendered == SERIES_COUNT" assertion and tests are updated. The existing `rejected{status,reason}` already counts the 401 itself.

### 5.9 Size and lint budget

`routes.py` net change is about -7 lines (counted in 5.5, including the `ChatRoute.unauth_audit` field and `gate=` argument added in commit 6, which is re-checked there; the move of `_ProducedModeHolder` lands in commit 5 and `check_sizes` runs in that same commit). `check_sizes`, `check_capacity_literals`, `check_no_module_mutable` (only `frozenset`/`MappingProxyType`/tuples at module level), `check_frozen_dataclasses` (all new value types are `@dataclass(frozen=True, slots=True)`; the gate is a plain class with `__slots__`. Note `check_frozen_dataclasses` is scoped to `plan`, `detect`, `resolve` only, so new `edge`/`audit`/`domain` value types are held to frozen+slots by a new AST test in `tests/edge/test_gw35_value_types.py`, not by that gate), `check_http_outside_edge_resolve`, `check_tenant_scale` are run per commit.

## 6. Control plane

### 6.1 `v2_audit_adapter.py`

`_RANK = {"allow":0,"flag":1,"redact":2,"error":3,"block":4}`, `_ACCEPTED = frozenset(_RANK) | {"rejected","shed"}`, ranking by `_RANK.get(a, -1)` (D-16, D-20). `translate`: console action must be in the six-word vocabulary else `RejectedV2("action")`; with no console, `outcome` must be in `_ACCEPTED` else `RejectedV2("action")`. `translate` also writes `metadata["refused_at"]` (D-21) when the trace has a `block`/`error` stage, and `metadata["key_fingerprint"]` from `detail` when present (string, 12 hex, else dropped). `merge_row` keeps "stricter wins" with the new ranks; when equal rank the incoming trace wins (existing behaviour).

### 6.2 Drain and attribution

`v2_audit_ingest` is unchanged in code: `_streams` already iterates `{rv2}:audit:*`, `_unauthenticated` is a stream like any other, and an unmapped org is skipped. `AMF_V2_AUDIT_ORG_MAP=dev-org=zeroshield,_unauthenticated=zeroshield` is the documented dev mapping (evidence file and `dev/` env example). `parse_org_map` already accepts a leading underscore; a unit test pins it.

### 6.3 Model, serializers, totals

`EnforcementEvent.action` is `CharField(128)`, confirmed by `core/models.py`; no migration. The design requires an enumeration audit by grep of `action__in|action ==|Count\(.*action` over `core/dashboard_views.py`, `policy/security_views.py`, `policy/views.py` and the threat-feed serializer; every result is classified in the evidence file as v1-only (untouched) or v2-visible (then `error` handled). The known v2-visible spot is `security_views.py:~1386`: `gateway_v2.total_threats` uses D-21, and two sibling keys `refused_total`, `error_total` are added. `useFirewallData.js` shows the two new numbers in `v2Note` only when non-zero.

### 6.4 Retired routes

`core/retired_routes.py` holds `RETIRED_ON_V2 = MappingProxyType({...})` (path → reason) for breaker state/trigger/reset, bedrock-test, db-test, MCP discover-tools, MCP tools-call and MCP OAuth start, and `retired_response(path)` returning HTTP 410 with body `{"status":"error","data":{"code":"retired_on_gateway_v2","message":<reason>,"path":<path>}}`. `gateway_admin_proxy_views._proxy` and the three `mcp_connector/views.py` forwarding sites check `AMF_GATEWAY_BACKEND == "v2"` and the path first; the check precedes the secret lookup and any network call. Default `v1` is byte-identical to today. `/v1/usage`, `/v1/observability`, `/v1/policy/check` (D-11) are added as ledger rows `decision: retire`, `owner: gw35`, `callers: []`.

## 7. Error handling

| Operation | Failure | Recoverable? | Caller receives | Log |
|---|---|---|---|---|
| `build_stages` / `console_from_holds` | any exception (including `SchemaError` from `validate_console`) | yes, additive | `None` → refusal/response unchanged and without trace | one WARNING per request (`amf.edge.console`), request id + exception class, no content |
| `apply_projection` | non-object body | yes | existing `ValueError` is caught by the callers' existing try; response unchanged | WARNING |
| `emit_chat_audit` | any exception, sink callback raising, queue full | yes | nothing; response already sent | none per record; the sink's own drop counter |
| `UnauthAuditGate.admit` | rate exceeded or capacity underivable | yes | record dropped, `amf_audit_unauthenticated_dropped_total` +1; **401/503 never delayed or altered** | ERROR once at boot when underivable |
| `validate_audit_record` in the control adapter | unknown action, evidence, missing field | no (record rejected) | `RejectedV2(reason)` counted by the ingest, not stored | existing ingest counter |
| `retired_response` | none (pure) | n/a | 410 | INFO once per call |
| Control setting `AMF_GATEWAY_BACKEND` invalid value (anything but `v1`/`v2`, case-sensitive; unset = `v1`) | fail fast: settings raise `ImproperlyConfigured` at start-up; no fallback, because falling back to `v1` would keep forwarding retired routes after a typo (requirement 7.2) | no (operator fixes config) | process does not start | ERROR with the offending value's repr |

Validation for external input: the bearer token is read only by `hashed_bearer_key` (existing shape check); the token never reaches `RequestHolds` except as the 12-hex fingerprint; the fingerprint is validated by `_check_fingerprint` (optional; 12 lowercase hex; otherwise `SchemaError`). `refuse_code` values are internal constants; unknown codes follow D-17 (policy) and 5.2 (no trace if no other fact).

## 8. Invariant ownership

- *Stage vocabulary and ordering*: `audit/console_schema.py` owns it; `edge` imports it; the frontend mirror is checked by the lint (R6.6). Owning it at the lowest layer that both the validator and builder share prevents drift.
- *A stage that ran and allowed is never `skip`*: the schema (`allow ⇒ ran`, `ran=False ⇒ skip|input_scan error`), plus the builder having no latency input.
- *Final action = max severity*: `console_schema.final_action`, called by both the builder and the validator.
- *Failure classification (error vs block)*: `domain/failure_class.py`, the single table.
- *No raw credential at rest*: the lifecycle site truncates to 12 hex; the schema rejects anything else; tests scan all output channels.
- *No tenant guess*: `emit_chat_audit._audit_org` uses `holds.org_id` or the reserved sentinel only.
- *No capacity literal*: `runtime/resources.py` only.
- *Response unchanged by the projection*: `console_from_holds` swallows and logs; `apply_projection` is applied to a finished response object.
- *Retire, not stub*: control `retired_routes`, drift-tested against the ledger.

## 9. Frontend

1. **Vocabulary mirror.** `pipelineTrace.js` exports `STAGE_ACTION_VOCAB = ["allow","flag","redact","block","error","skip"]` (literal array). `honestStageAction` returns `error` unchanged for `ran:false`+`error` (the plan_unavailable pair) and never maps `ran:true` to `skip` (unchanged).
2. **`isGatewayV2Trace(body)`** (new, `pipelineTrace.js`): true when `body.source === "gateway_v2"` (live simulator response / SSE `amf.trace` frame, D-27) or, for threat-feed events, `event.metadata.source === "gateway_v2"`; false otherwise (v1 never sets it). Stage count is never the discriminator. `liveGateway.js` keeps `stages.length >= 6` and, for v2 traces, bypasses the nine-stage template and the fabricated-stage paths (`enrichStages` must not add `kill_switch`, `policy`, `model_input`; asserted by test). v1 events (no `source`) are untouched.
3. **`error` rendering.** `STAGE_LABELS` gets `error`-aware copy; `StageTimeline.jsx` theme, `LogDetailPage.jsx::StatusBadge`, the threat-feed badge in `firewall-module-utils.js`, `liveGateway.js::inferFinalAction`/`blockingStageName` (adds `blockingStageName` counterpart `erroringStageName`), and the Attack Simulator result card (`AttackSimulatorPanel.jsx`) render `error` with label "Upstream error" in the existing error colour and never as BLOCK or "No threat detected". `inferFinalAction` prefers `pipeline_trace.final_action` for v2 traces (already first), so a v2 `error` is not re-derived from the HTTP status.
4. **Not-reported fields.** For v2 traces `LogDetailPage` and the simulator replace latency/TTFT/Duration, routing card, `guard_summary`, `scan_outcome`, token counts and risk score with "Not reported by gateway v2" (new `notReported(trace)` helper returning the string); no `0`, no default. Done through `resolveTotalLatencyMs` returning `null` for v2 traces without `total_latency_ms`, which the pages already treat as "no value".
5. **Retired panel.** New `utils/retiredRoute.js` with `isRetiredResponse(status, body)` and `retiredPanelModel(body)`; new `components/RetiredOnGatewayV2.jsx` renders the labelled panel. `CircuitBreakerSimulator.jsx`, `IsolationOpsSimulator.jsx`, `BedrockTestPanel.jsx`, `DatabaseConnectionPanel.jsx`, `MCPConnectorPanel.jsx`, `MCPManagerPanel.jsx` call `isRetiredResponse` on their fetch result and render the panel instead of a spinner/empty chart.
6. **Page audit (R6.3).** The audit list is the diff between the regenerated `frontend-verifier/fields.json` and the v2 contract set (section 10, step 2), written into `evidence/…/page-audit.md`; each flagged read gets a `source` branch, a "not reported" substitute, or a v1-only ledger entry.

## 10. Inventory and lint plan

1. **Baseline commit first.** Regenerate with `python -m lint.check_console_inventory --write` on the unmodified branch head and commit alone as `chore(gw35): regenerate GW14b ledgers for baseline drift`; review its diff and record each changed row (the progress file documents this drift). This isolates pre-existing drift from this card's changes.
2. **Contract diff.** Add `lint/console_inventory.py::v2_contract_reads()` listing the contract fields (`zeroshield.{action,request_id}`, `pipeline_trace.{final_action,plan_version,stages[].{name,action,ran,deciding_rule}}`, `source`, headers, `console_schema` keys) and a `--contract-diff` mode that prints frontend reads outside the set that lack a `source !== "gateway_v2"` guard or a v1-only ledger note. Its output is attached to the evidence.
3. **Vocabulary check (R6.6).** `check_console_inventory` parses `STAGE_ACTION_VOCAB` from `audit/console_schema.py` (AST, literal tuple) and from `frontend/src/utils/pipelineTrace.js` (regex over the literal array) and fails when the sets differ.
4. **Ledger changes.** `admin_ledger.py` accepts `signed` (null or object) and `decision_ref`; unsigned rows print "pending signature" and exit 0; `--require-signed` exits non-zero. Add rows for `/v1/usage`, `/v1/observability`, `/v1/policy/check`. `cp-consumers/endpoints.json` gains consumer rows for the `error` rank and the `_unauthenticated` stream (R6.5). Then regenerate (second commit) and review.
5. **Doc test (R5.5).** `tests/lint/test_stage_wording.py` greps `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, `docs/plans/**/*.md` GW14b plan text and the progress file for `circuit_breaker` used as a dropped stage next to `kill_switch`/`model_input`, and the docs are corrected to `kill_switch, policy, model_input`, each marked "proposed, awaiting owner signature". The test file must not itself contain the stale phrase.

## 11. Test and gate plan

**Gateway unit (pytest, no network):**
- `tests/audit/test_console_schema_gw35.py`: vocabulary, `error` accepted, unknown stage name rejected (`kill_switch`), `allow ⇒ ran`, `skip ⇒ ¬ran`, `error ∧ ¬ran` only on `input_scan`, optional `plan_version` rules, final-action mismatch rejected, fingerprint/hash checks, AST test that `console_projection.py` has no `"allow"` literal as the second argument of an auth/rate_limit `StageOutcome`.
- `tests/edge/test_console_stages.py`: one example per row of the requirement 1.2/1.3/2.4 tables; each refused code yields the exact six-stage array.
- `tests/domain/test_failure_class.py`: total-coverage test **scoped to the codes that can reach the output side or the pre-dispatch step**: every `domain.posture` stream code, every `domain.posture` code passed to `kill_latch.kill` in `routes.py` (round 4, finding 2), every `upstream_*` code in `edge/errors.py`/`dispatch/provider_errors.py`, `output_blocked`, `scan_failure`, `policy_regex_timeout`, plus the `INPUT_SIDE` and `PREDISPATCH_OPERATIONAL` tables, must be in exactly one of operational, policy, `NO_ACTION`, `INPUT_SIDE`. The test does not claim coverage of request/MCP codes in `errors.py`; those use the documented unknown-is-policy default (a separate test asserts that default). For every classified code outside `NO_ACTION`, `build_stages` with the code set never yields `final_action == allow`; for `NO_ACTION` codes the console equals the no-code console.
- `tests/edge/test_gw35_predispatch_failure.py` additions (round 3): `input_unmaskable` → `input_scan: block` (rule `input_unmaskable`), routing/output `skip`, `final_action: block`; `lifecycle_incomplete`, `credential_unavailable`, `provider_unreachable` → `model_routing: error`, `final_action: error`.
- `tests/edge/test_gw35_kill_switch.py` (round 3): a kill-switch cut on both `StreamPipeline` and `StrictWithholdPipeline` leaves the console and `X-ZeroShield-Action` equal to the pre-change golden; no `failure_code` is recorded.
- `tests/edge/test_gw35_stream_operational.py` (round 4): on `StreamPipeline` (incremental), stream malformed upstream and inter-chunk timeout before any released byte → `model_output: error`, `output_guardrail: skip`; after a recorded synthetic ALLOW → `output_guardrail: allow, ran`, `model_output: error`, `final_action: error`; `note_output_action("error")` is ignored; a policy cut (`stream_key_revoked`) → `output_guardrail: block`; in-stream `plan_unavailable` → `model_routing: error ran=true`, `output_guardrail: skip`, `final_action: error`; frame, audit record and header agree; no stage other than `model_output`/`model_routing` shows `error` for an operational cause (P2 extended).
- `tests/edge/test_gw35_strict_failures.py` (round 3): strict mode rows: malformed upstream and inter-chunk timeout → `model_output: error`, `output_guardrail: skip`, `final_action: error`; `strict_buffer_overflow` → `output_guardrail: block`; frame, audit record and header agree; `note_output_action(None)` and `("")` leave holds unchanged.
- `tests/edge/test_holds_gw35.py`: slots, write-once failure, no `__dict__`.
- `tests/edge/test_unauth_audit.py`: gate bound, drop counting, underivable capacity drops, metrics series.

**Gateway integration (in-process ASGI, existing harness):**
- `tests/edge/test_gw35_refusal_traces.py`: for each of the six codes (HTTP and stream where applicable): status/body/`x-request-id` byte-identical to a golden capture of the current build aside from `zeroshield`/`pipeline_trace` (golden captured before the code change in the first commit); trace shape; `X-ZeroShield-Action`; `stream:true` identity refusal is plain HTTP.
- `tests/edge/test_gw35_upstream_error.py`: non-stream `upstream_error`/`upstream_rate_limited`, stream malformed upstream, inter-chunk timeout; asserts `model_output: error`, `output_guardrail: skip`, `final_action: error`, HTTP unchanged; an `output_blocked` case stays `block`; a client disconnect is neither.
- `tests/edge/test_gw35_failed_credential_audit.py`: revoked key, junk token, missing header → one admission record, outcome `rejected`, reason, status 401, org `_unauthenticated`, console `auth: block`; 503 identity doubt → `auth: error`; record `detail` carries the 12-hex fingerprint only; raw token absent from serialized records, captured log output, and `/metrics` text; sink callback that raises leaves the 401 intact; gate saturation leaves the 401 intact and increments the drop series.
- `tests/edge/test_gw35_plan_unavailable.py` (round 1 finding 1, round 2 finding 2): `plan_unavailable` yields non-empty `zeroshield.request_id`, an audit record under the identity's org, `x-request-id` header and error-body request id byte-identical to the golden, `rate_limit: skip`, `input_scan: error ran=false`.
- `tests/edge/test_gw35_responses_route.py` (finding 2): failed credential on `/v1/responses` → `_unauthenticated` record, `auth: block`, 401 unchanged; Responses lifecycle refusal console equals its audit console; MCP failed credential produces no `_unauthenticated` record (D-31).
- `tests/edge/test_gw35_audit_equals_response.py` (finding 3): audit console == response console for every code, HTTP and stream.
- `tests/edge/test_gw35_predispatch_failure.py` (finding 4): `stream_buffer_unavailable` and in-stream `plan_unavailable` → `model_routing: error ran=true`, `model_output: skip`, `output_guardrail: skip`, `final_action: error`, never `allow`; code set with `dispatched` false and no input decision (invalid key, shed, rate-limited, budget, lifecycle `plan_unavailable`, input `scan_failure`) → `model_routing`/`model_output` both `skip, ran=false` (round 4, finding 3).
- `tests/edge/test_gw35_source_carrier.py` (finding 6): body keys added are exactly `{zeroshield, pipeline_trace, source}`; `amf.trace` frame carries `source`.
- `tests/audit/test_console_schema_gw35.py::test_mcp_console_still_validates` plus the existing MCP console tests (`tests/edge/test_mcp*`) run in every gate: `tool_call` accepted with `MCP_STAGE_NAMES`, rejected for chat (round 2, finding 1).
- `tests/edge/test_gw35_value_types.py` (finding 7): AST/runtime check that every new value type is frozen+slots and `UnauthAuditGate` defines `__slots__` with no `__dict__`.
- `tests/audit/test_unauth_org_stream.py`: `AuditSink` writes and `audit_stream("_unauthenticated")` reads back one record (org is a free string; verified in `audit/sink.py` and `store_keys.audit_stream`), and its byte share is bounded like any org.
- Perf: `tests/edge/test_console_projection_cost.py` times `console_from_holds` over a fixed set (median and p99, ratio against `chat_console`'s pre-change cost recorded as a number in evidence, no capacity literal asserted in code; the assertion is relative: after ≤ 3× before and no I/O, checked by running under a socket-blocking fixture).

**Property tests (seeded `random.Random`, tagged `# Feature: gw35-console-truth, Property N: …`):**
- P1 (≥1000 it.): for random `ChatFacts` consistent with a lifecycle prefix, six stages in canonical order; first non-allow stage is the refusing one; every later stage `skip`/`ran:false`; output validates.
- P2 (≥1000): over (input disposition × provider outcome × output disposition × failure code) `final_action == error` iff provider failed and no `block` decided; `output_guardrail == block` only if an output block was decided.
- P3 (≥500): generated consoles pass `validate_console`; single-field mutations (renamed stage, flipped `ran`, wrong final) fail it.
- P4 (≥200, ASGI): random 48-char tokens through failed requests never appear in any output channel; fingerprint is always `sha256(token)[:12]`.
- P5 (≥500): gate admits ≤ `rate` per window for random arrival sequences; `accepted + dropped == offered`.
- P6 (≥500, control, no Django): random action sequences merged in random order yield the max-rank action, and `translate` rejects every action outside the vocabulary.
- P7 (≥200): `console_from_holds` over random garbage `RequestHolds` never raises and returns `None` or a valid console.
Each property is run through a helper `run_property(fn, n, seed)` so the negative controls can call it.

**Negative controls (R8.7):** `tests/edge/test_gw35_negative_controls.py` monkeypatches (a) `console_stages.build_stages` auth/rate_limit rule back to a literal allow and asserts P1 and the revoked-key test fail; (b) `failure_class.is_operational_failure` to `lambda _: False` and asserts P2 and the upstream-error test fail; (c) `audit_emit._audit_org` back to the legacy "return None unless holds.org_id" and asserts the failed-credential test fails. Each asserts the failing exception type so a vacuous pass is impossible.

**Control (Django container, as the progress file does):** `core/tests/test_v2_audit_adapter_gw35.py` (rank, `error`/`block` merge, `rejected`/`shed` still rank -1 with a merge test over every no-console producer listed in D-20, unknown action rejected, `refused_at`, fingerprint pass-through, `_unauthenticated=` org map parse, P6), `policy/tests/test_v2_totals_gw35.py` (threat totals per D-21, `error` row in the feed), `core/tests/test_retired_routes.py` (410 body, invalid `AMF_GATEWAY_BACKEND` raises `ImproperlyConfigured`, no `requests` call under `v2`, byte-identical `v1` default). The pure adapter tests also run on the host without Django; the evidence file states which suites ran where.

**Frontend (`node --test`):** `pipelineTrace.test.js` (vocabulary mirror, `error` severity/label, `ran:false`+`error`), `liveGateway.test.js` (v2 pre-scan refusal trace stays on the six-stage path; `error` final action and blocking-stage helpers; no fabricated stages; v1 events unchanged), `firewall-module-utils.test.js` (feed row with `error` and with a pre-scan refusal), new `retiredRoute.test.js` (410 detection, panel model, and a source check that each of the six caller components imports it), simulator-card mapping tests.

**Lint/gates, all recorded with counts in `docs/plans/evidence/2026-11-04-gw35/`:** full offline pytest; `mypy --strict`; `ruff`; `import-linter` (both contracts); the five AST gates plus `check_sizes`; `python -m lint.check_console_inventory` (green after regeneration); `tests/lint/test_console_inventory.py`; `npm run lint`, `npm run test:unit`, `npm run build` (in a `node:22-alpine` container if the host has no node). Pre-existing failures are recorded with their baseline from the first (golden/baseline) commit.

**Not run, left for the human:** L35-1 Playwright sweep, L35-2 on the live stack, L14b-3 browser walk, owner signatures.

## 12. Commit order (bottom-up; each commit green on its own gates)

1. Baseline: golden captures of today's refusal/upstream responses; regenerated ledgers for existing drift.
2. `domain/failure_class.py` + `runtime/resources.py::unauth_audit_rate` + tests.
3. `audit/console_schema.py`, `audit/record.py` + schema tests, negative-control scaffolding.
4. `edge/request_holds.py`, `edge/console_stages.py`, `edge/console_projection.py` (+ P1, P2, P3, P7).
5. Lifecycle/non-stream/stream wiring (`openai_chat`, `openai_nonstream`, `egress/stream`, `routes`, `produced_holder`) + integration tests.
6. `edge/unauth_audit.py`, `audit_emit.py`, publisher series + failed-credential tests (+ P4, P5).
7. Control adapter/totals/retired routes + tests (+ P6).
8. Frontend changes + tests, retired panel.
9. Lint extensions, ledger rows, doc wording fix, regenerated ledgers, page-audit evidence, final gate run and evidence file.

## 13. Edge cases

- Streamed refusal after identity but before first byte: SSE `error` frame then a trailing `amf.trace` frame (existing mechanism), now with the pre-scan trace.
- Retried streams (`run_with_no_splice`): `mark_dispatched` is idempotent; `note_failure` is write-once, so the first attempt's cause is kept only if no byte was released; a successful retry clears nothing because failure is only noted when the final stats show it.
- Request that spends budget, fails `scan_failure`: `rate_limit allow`, `input_scan error ran`.
- A `redact` input followed by an upstream failure: final `error` (D-16); the input stage keeps `redact`.
- Duplicate audit emit for one request: unchanged (single `finally`).
- Control re-ingest of old rows with `rejected`/`shed` action and no console: still rank -1, unchanged (D-20).
- A trace with `plan_version` missing arriving at an old frontend: it reads `plan_version` only for display, guarded with optional chaining and tested.

## 14. Known gaps recorded, not fixed

D-06 parse/pre-check refusals have no trace; unauthenticated MCP calls are not audited (D-31); D-18 a provider raise that is not `UpstreamFailed` still reads `scan_failure`/block; `IdentityCache` does not remember revoked-key owners (D-19); the multiplier behind `unauth_audit_rate` is unmeasured (D-26); `state_control/valkey.py:100` and the `BudgetLease._refill` warning are untouched.

## 15. Open questions

None. Every ambiguity above is resolved by a numbered decision (D-15 to D-26) or recorded in section 14.

## 16. Response to design-review findings

### Round 4 (this revision)

1. HIGH, operational stream failures painted `output_guardrail: error`: fixed. `output_action` now carries dispositions only; `record_cut` and `note_stream_output` call `note_output_action` only for policy cuts and never forward `error`; `note_output_action` ignores non-dispositions; rule 5(a) is restricted to `{allow, flag, redact, block}`; the "identical consoles" claim is corrected, with the pass-through allow-then-error row stated and tested (5.2, 5.7, 11).
2. MEDIUM, in-stream `plan_unavailable` defaulted to policy: fixed. `posture.PLAN_UNAVAILABLE` is in `PREDISPATCH_OPERATIONAL` (D-17); the coverage test now includes every code `routes.py` passes to `kill_latch.kill`; with finding 1 applied `record_cut` writes no output action for it.
3. NIT, rule 4 gap: fixed. Explicit branch: `dispatched` false, a code set, no input decision → `model_routing` and `model_output` both `skip, ran=false`; test row added.

### Round 3

1. MEDIUM, kill-switch cut painted as policy block: fixed. `NO_ACTION = {"killed", STREAM_KILLED}` and `is_no_action` in `domain/failure_class.py` (D-17); `note_stream_output` and `record_cut` skip both writers for it; rule 0 in 5.2; invariant restated as "a code outside `NO_ACTION`"; kill-switch test on both pipelines added.
2. MEDIUM, input-side and internal pre-dispatch refusals painted on `model_routing`: fixed. `INPUT_SIDE` table checked first in rule 4 (`input_scan: block`, later stages skip); `PREDISPATCH_OPERATIONAL` listed explicitly (`model_routing: error`); examples added to tests.
3. MEDIUM, STRICT path unspecified: fixed in 5.7. `note_output_action` is called only for a non-`None` action and ignores empty input; the strict pipeline is unedited and relies on `failure_code` alone, with the resulting stages stated per cause; strict test rows added.
4. NIT, coverage test vs unknown-is-policy default: fixed. The test is scoped to output-side and pre-dispatch codes; a separate test pins the unknown default.

### Round 2

1. HIGH, MCP consoles rejected by the shared validator: fixed. `validate_console(console, *, stage_names=STAGE_NAMES)`; `MCP_STAGE_NAMES` exported from `audit/console_schema.py` and re-used by `mcp_console`; ordering and name rules apply to the passed tuple; applicability of each new rule to MCP stated in 5.1; `test_mcp_console_still_validates` plus existing MCP tests added to the gate.
2. HIGH, `refusal_request_id()` returns `None` for `plan_unavailable`: fixed. New code-independent `mint_request_id()`; `refusal_request_id` delegates and is behaviourally unchanged; D-30 and 5.5 use `mint_request_id()`.
3. MEDIUM, stream cuts never reach `holds`: fixed in 5.3/5.7. `produced_holder.record_cut` writes `note_failure` and `note_output_action` in the kill-latch branch; `note_output_action` is last-write-wins and beats the synthetic ALLOW Decision; audit output record uses the same facts; `stream_trace_payload` reads only `holds`; per-cut-reason agreement test added.
4. MEDIUM, dispatched policy codes without a decision gave `allow`: fixed. Rule 5(b) maps them to `output_guardrail: block, ran`; `policy_regex_timeout` added to the policy list; total-coverage test over `edge/errors.py` and `domain.posture` stream codes asserts `final_action != allow` whenever a code is set.
5. MEDIUM, `AMF_GATEWAY_BACKEND` contradiction: fixed. Fail fast with `ImproperlyConfigured`, no fallback; test added.
6. NIT, `routes.py` budget: recounted (about -7, ≈ 792) including the `ChatRoute` field and `gate=` argument; `check_sizes` runs in the commit that moves `_ProducedModeHolder` and again in commit 6.
7. NIT, `refusal_console(None, code)`: returns `None`; `note_request` precedes the console build; stated in 5.3 and tested.

### Round 1 (kept for the record)

1. HIGH, plan_unavailable ids: addressed by D-30 and 5.5. Identity org and a minted `zs-` id are noted on `holds` before the refusal returns; `x-request-id` and body request id are untouched; the minted id lives only in `zeroshield.request_id` and the audit record; `validate_console` rejects an empty `request_id`; test `test_gw35_plan_unavailable.py`.
2. MEDIUM, other callers: addressed by D-31, the layer table (`openai_responses.py` added) and 5.3. Sentinel org is limited to `route == "chat"`; MCP excluded and recorded as a known gap; Responses regression test added.
3. MEDIUM, facts derivation: addressed by D-28 in 5.3. Exact precedence `refuse_code or holds.failure_code`, split by `dispatched`; `note_failure` at every refusal site; audit==response test for every code.
4. MEDIUM, pre-dispatch refusals: addressed by D-29 (Option 1). `model_routing` carries the failure with `ran=true`; rule 2 corrected so in-stream `plan_unavailable` reads `rate_limit: allow`; P2 gains a "never `allow` with a code" case.
5. MEDIUM, ranking claim: addressed by rewriting D-20. `rejected`/`shed` stay at -1 (no behaviour change); acceptance moves to a separate `_ACCEPTED` set; producers enumerated; merge test added. The false "unchanged as block" claim is removed.
6. MEDIUM, v2 discriminator: addressed by D-27. Body `source: "gateway_v2"` is added by `apply_projection` (and the `amf.trace` frame); threat feed uses `metadata.source`; req 2.6 allow-list extended to `{zeroshield, pipeline_trace, source}` as a documented decision; `isGatewayV2Trace(body)` never uses stage count.
7. NIT, gate class: fixed. Plain `__slots__` class; the `check_frozen_dataclasses` scope claim corrected and a dedicated AST test added.
8. NIT, line budget: fixed. 799 now, about 22 removed and about 11 added, planned ≈ 788, `check_sizes` in commit 5.
Unverified assumption on `_unauthenticated` stream: checked; `AuditRecord.org_id` is a free non-empty string and `audit_stream(org)` formats it, so it is accepted; a test is added.
