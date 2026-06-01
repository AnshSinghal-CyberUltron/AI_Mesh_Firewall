# Module 1 — Full-Quarter Implementation Roadmap

> Owner: AMF eng. Status: DRAFT v1 (post-brainstorming, pre-triage). Horizon: 8–12 weeks. Compose project: `ai_mesh_firewall`.

---

## 0. Executive context (why this doc exists)

The user requested "fully implement Module 1 (1.1–1.7) per spec". A read-only audit of `/AI_Mesh_Firewall/` performed by four parallel Explore subagents established that the codebase is **~75% built** with file:line evidence, not the ~30% my earlier reality-check implied. This roadmap is therefore a **gap-closure plan over one quarter**, not a green-field rebuild.

**Inputs to this roadmap:**
- Module 1 specification (1.1 ingress, 1.2 RAG firewall, 1.3 vector DB, 1.4 MCP, 1.5 multi-model, 1.6 kill-switch, 1.7 output guardrails) — pasted earlier in session.
- Four read-only audits (see `triage_findings_consolidated.md`) covering all 7 sub-controls + cross-cutting infra (tier-1, tier-2, policy, audit, forwarding).
- Five adversarial triage outputs from the previous verification cycle (Security Hawk, Pragmatist, Evidence Auditor, Independence Purist, Verification Rigourist).
- User constraint: tier-1 (regex/keyword) → if policy=allow|redact → tier-2 (LLM scan) → forward to org inference model.
- Verification bar: hard Playwright + curl oracle producing real artefacts (response body + redis key + audit log) per gap.

**Operating contract (applies to every phase):**
1. Draft a written decision per gap with explicit **reason**, ≥3 alternatives considered, trade-offs, files touched, risk, verification oracle. *No code.*
2. Dispatch 5 parallel adversarial triage agents (`runSubagent` Explore with hostile lenses) — **wait for all five**.
3. Reconcile into `final_decision_<phase>.md` (ACCEPTED / REJECTED / DEFERRED per item, with reason).
4. Implement against the final decision (TDD where feasible).
5. Run `mcp_ruflo_aidefence_scan` on the diff.
6. Re-dispatch 5 contradicting triage agents on the diff.
7. Live verification (hard oracle) per gap.

---

## 1. Real Module 1 gaps (the only things to build)

The 14 gaps below come directly from the audit. Each is anchored to file:line. **No spec item is dropped; the rest is already wired.**

| ID | Sub-control | Gap (audit evidence) | Severity | Phase |
|---|---|---|---|---|
| G1 | Cross-cutting | Gateway loads policy bundle without verifying HMAC. `gateway/policy_sync.py` — `verify_bundle()` exists but not called on load path. Redis-MITM → arbitrary policy injection. | **CRIT** | P0 |
| G2 | 1.2 | `ENABLE_TIER2` is a single process-wide env var. One flag silently disables semantic detection for all orgs. No per-org / per-policy override. `gateway/scanner.py:338`. | **HIGH** | P0 |
| G3 | 1.2 / cross-cutting | Tier-2 Bedrock failures silently fall back to Tier-1 only (`scanner.py:768-785`). No circuit breaker, no alert, no retry policy. A Bedrock outage = silent security downgrade. | **HIGH** | P0 |
| G4 | 1.2 + 1.5 | `model_downgrade` scanner verdict exists but no routing code consumes it — `ACTION_ORDER` includes it (`main.py:258-280`) but `llm_router.select_model()` never reads the scanner verdict. | MED | P1 |
| G5 | 1.2 | RAG query rewrite happens in `rag_pipeline/query_stage.py:91+` but rewritten query is not returned to caller / propagated. Retriever gets the original prompt. | MED | P1 |
| G6 | 1.3 | `sensitive_fields` on `VectorPolicy` is captured by UI + serializer but `ranker_stage.py` and `retriever_stage.py` do not filter retrieved chunks by these field names. | MED | P1 |
| G7 | 1.4 | MCP `redaction_fields` accepted by `MCPManagerPanel.jsx:632`, stored, but `mcp_connector/views.py` tool-call handler does not apply field-level redaction before returning tool output. | MED | P1 |
| G8 | 1.4 | Context allowlists are tenant-level only — no per-user / per-agent / per-role data scope. `mcp_proxy.py:358` shows org-scoped isolation only. | MED | P1 |
| G9 | 1.6 | `circuit_breaker.py` tracks model error rate but does not auto-trip the kill-switch when breaker opens. Operator must intervene manually. | MED | P1 |
| G10 | 1.7 | Hallucination grounding score is lexical overlap only (`output_guard.py:293-303`). No embedding-similarity check against retrieved context — high false-negative on paraphrase. | MED | P1 |
| G11 | Cross-cutting | `EnforcementEvent` is written to Postgres + Mongo but **no admin API or UI endpoint** exposes queryable events. Compliance team is blind. `backend/policy/evaluation_views.py:464` creates events; no list/search route. | LOW | P2 |
| G12 | Cross-cutting | No end-to-end request-ID correlation across gateway → backend → LLM. UUIDs generated ad-hoc; audit-trail correlation is best-effort string match. | LOW | P2 |
| G13 | Cross-cutting | Policy bundle published via Redis Pub/Sub **in plaintext** (only HMAC-signed, not encrypted). Anyone with Redis read can enumerate detection rule signatures. `backend/policy/compiler.py:60+`. | LOW | P2 |
| G14 | Tier-1 | Advanced obfuscation bypass: zero-width-character split + leet combinations escape `_deobfuscate_text()` (`gateway/scanner.py:456-475`). | LOW | P2 |

---

## 2. Phasing — three sprints over the quarter

### Phase 0 — Security-critical, this cycle (G1, G2, G3)

**Why P0 first:** All three are *currently exploitable in production*. G1 lets any Redis-write actor inject malicious policies (immediate auth bypass). G2 is one env-toggle away from disabling semantic security org-wide. G3 turns any Bedrock outage into a silent capability downgrade with no observability. None require architectural change; each is a surgical contained edit. Building anything else before these is irresponsible.

**Exit criteria:**
- G1: gateway refuses to load any policy bundle whose HMAC fails verification; live oracle proves a tampered bundle is rejected and an alert event is logged.
- G2: a per-org config field overrides `ENABLE_TIER2`; UI exposes a toggle; live oracle proves org A sees Tier-2 while org B does not, with both governed by the same gateway process.
- G3: gateway tracks Bedrock failure rate per minute; when threshold breached, opens a circuit (configurable: fail-closed = block, fail-open = downgrade with audit + alert); live oracle simulates Bedrock 5xx and proves the chosen path runs.

### Phase 1 — Enforcement gaps, next sprint (G4–G10)

**Why second:** All are *feature completions* of already-built control surfaces (UI + schema exist; routing + enforcement is missing). They make stored configuration actually do work. Sequenced after P0 because none of them are presently exploitable — they are non-enforcement, not anti-enforcement.

**Logical sub-grouping for parallel execution within the sprint:**
- Routing-layer (G4 + G9) — single agent owns `llm_router.py` + `circuit_breaker.py` + `kill_switch.py` interaction.
- RAG pipeline (G5 + G6) — single agent owns `rag_pipeline/*` + ranker filtering.
- MCP/context (G7 + G8) — single agent owns `mcp_connector/views.py` + `mcp_proxy.py` allowlist plumbing.
- Output guard (G10) — single agent owns `output_guard.py` grounding upgrade (sentence-transformers embedding similarity).

### Phase 2 — Observability + hardening, third sprint (G11–G14)

**Why last:** Compliance/observability is high value but not security-blocking. G14 (advanced obfuscation) is a security hardening but only relevant *after* G1/G2/G3 — there's no point hardening Tier-1 obfuscation while the policy bundle itself can be replaced (G1).

**Sub-grouping:**
- API + UI (G11 + G12) — backend list/search endpoints for `EnforcementEvent`, request-ID propagation middleware end-to-end.
- Crypto (G13) — Pub/Sub channel encryption (AES-GCM keyed off `POLICY_SIGNING_KEY` derivation or explicit `POLICY_BUNDLE_KEY`).
- Detection (G14) — extend `_deobfuscate_text()` with zero-width strip + iterative leet pass + length-bounded recursive decode.

---

## 3. Cross-phase architectural decisions (call out now, lock per-phase later)

**A. Per-org configuration plane.** Today many gates are global env vars (ENABLE_TIER2, BEDROCK_*, threshold values). The cleanest unifying change for G2 + G3 + G6 is to **promote these to org-scoped config rows** read via the existing `CONFIG_SYNC` cache. Decision must be triage-tested: alternative is per-policy fields, which couples policy editing to operational toggles. Default proposal: org-scoped config table with policy-level override, per-org-default fallback to env var.

**B. Audit event sink.** G1 alert, G3 alert, G9 auto-trip event, G11 query endpoint all imply a *unified* audit-event sink with structured severity. Today telemetry uses RabbitMQ → Mongo for enforcement; we should not invent a parallel system. Decision: extend `EnforcementEvent` schema with `event_class` enum ("enforcement" | "operational_alert" | "kill_switch_trip") rather than adding a new model.

**C. Verification harness.** The verification cycle already has Playwright + curl oracles. Each gap below ships with one hard oracle. We will **not** add unit tests as the primary proof per user's verification-bar choice — but for code where TDD is the cleanest entry (e.g., HMAC verify in G1), a unit test is written first as a development aid, with the hard oracle as the merge gate.

**D. Backward compatibility.** Each change must default to *current behaviour* when new config is absent (zero-impact rollout). E.g., HMAC verify defaults to *required* but emits a deprecation warning for one release if `POLICY_REQUIRE_HMAC=false`; per-org Tier-2 falls back to env var if no org override is set.

**E. Standalone product invariant.** No change may import from `/Users/anshsinghal/Desktop/AI_Security/{backend,gateway,shared}` parent paths. AMF must remain `extract`-buildable as a standalone product (Independence Purist contract from prior triage cycle).

---

## 4. Phase 0 — decision drafts (this session)

Each decision below is the **draft** that will go through the 5-agent contradiction triage before any code is touched.

### Decision D-G1 — Enforce HMAC verification on policy bundle load

**Problem:** `gateway/policy_sync.py` reads compiled policy JSON from Redis keys `policies:compiled:{org_slug}` and applies them to the org cache without calling `verify_bundle()` from `gateway/policy_signing.py`. An actor with Redis write access (e.g., compromised worker, lateral movement from any compose service sharing the redis network) can inject arbitrary policies — including `action="allow"` policies that completely disable enforcement.

**Decision:** On every load, call `verify_bundle(bundle, signing_key)`. If verification fails:
- **Fail-closed default** (`POLICY_REQUIRE_HMAC=true`, the new default): refuse to update cache; keep prior loaded bundle if any; emit a CRITICAL operational alert (new event_class) tagged `policy_hmac_failure`; if no prior bundle exists, the org's gateway path returns 503 fail-closed (this matches the existing "policy cache require loaded" semantics in `main.py:510-520`).
- **Permissive transitional** (`POLICY_REQUIRE_HMAC=false`): log a warning, accept bundle, emit a `policy_hmac_warning` event. This mode exists *only* so existing deployments without `POLICY_SIGNING_KEY` rotation can upgrade without an outage; default is `true`.

**Alternatives considered:**
1. **Sign at consumer instead of producer** (gateway signs upon receipt). Rejected — defeats purpose; an attacker still injects the unsigned bundle which then becomes "signed by gateway".
2. **TLS-only Redis with mutual auth, skip HMAC.** Rejected — would be additive, not substitutive; doesn't defend against a malicious worker that already speaks Redis legitimately.
3. **AES-GCM encrypt bundle (defers G13).** Rejected for P0 — broader change, ties G1 to G13, slips deadline. AES-GCM in G13 will sit *on top of* HMAC; the AAD will include the HMAC tag.

**Files touched:**
- `gateway/policy_sync.py` — add verify on load (estimated <40 lines).
- `gateway/policy_signing.py` — confirm `verify_bundle()` is robust against malformed JSON / missing `_sig` (returns `False`, never throws).
- `backend/policy/models.py` — add `event_class` choices, including `policy_hmac_failure`, `policy_hmac_warning` (migration).
- `gateway/main.py` — wire alert emission helper that produces an `EnforcementEvent` with no `policy_id`/`rule_id` (operational event).
- Env: `POLICY_REQUIRE_HMAC` (default `true`), `POLICY_SIGNING_KEY` (already exists).

**Risk:** Bundles signed with a previous key (key rotation in flight) will be rejected. Mitigation: `POLICY_SIGNING_KEY` supports comma-separated list of valid keys (primary + N grace keys), verifier accepts any. Document the rotation procedure.

**Verification oracle (hard):**
1. `redis-cli SET policies:compiled:zero-shield '{"_sig":"deadbeef",...}'` (corrupt sig).
2. Trigger `policy_sync` refresh (publish to channel).
3. `curl /v1/chat/completions` with malicious prompt — must still receive **block** verdict from the *previously valid* cached bundle.
4. Inspect MongoDB `enforcement_events` collection — must contain one event with `event_class="policy_hmac_failure"`, `severity="critical"`.
5. Delete redis key entirely, repeat curl — must return 503 with safe error body.

---

### Decision D-G2 — Per-org Tier-2 enable override

**Problem:** `ENABLE_TIER2` is read once at gateway startup (`scanner.py:338`). Operations cannot enable Tier-2 selectively for a regulated tenant while leaving it off for a cost-sensitive tenant on the same gateway. Worse, a config flip-off cascades to every org silently.

**Decision:** Introduce per-org Tier-2 configuration:
- New field on `Organization` (or its `GatewayConfig` sidecar): `tier2_enabled: bool | null` (null = follow process default).
- New field: `tier2_strict: bool` (when true and Bedrock fails, block instead of degrading — couples with G3).
- `CONFIG_SYNC.get_config(org_slug)` returns these; `scanner.scan_prompt_with_tier2()` consults the org override before falling back to the global `ENABLE_TIER2`.
- UI exposes a toggle in Org Settings (frontend `OrgSettingsPanel` if it exists; else add a new tab in `SettingsPanel`).
- API: `PATCH /api/orgs/{slug}/gateway-config` accepts these fields, requires `org_admin` role.

**Alternatives considered:**
1. **Policy-domain level toggle** (a policy says "require tier2"). Rejected — couples ops to policy author; ops shouldn't have to write a policy to flip a feature flag. Mixes editorial intent with operational state.
2. **Per-API-key toggle.** Rejected — too fine-grained, no clear admin owner, leads to drift.
3. **One global flag with allowlist of opted-in orgs (env var list).** Rejected — config-by-env-var doesn't scale; UI cannot expose; rollback is a redeploy.

**Files touched:**
- `backend/core/models.py` — extend `Organization` (or `GatewayConfig` model) with two BooleanFields; migration.
- `backend/core/serializers.py` + view — expose via REST.
- `gateway/scanner.py:338` — change `scan_prompt_with_tier2()` to accept `org_overrides: dict | None` parameter; main.py passes from `auth_ctx`.
- `gateway/main.py` — at call site (line ~1925 area), pull tier2 fields from org config and pass.
- `frontend/src/components/OrgSettingsPanel.jsx` (new or extended) — toggles.
- Env unchanged; `ENABLE_TIER2` becomes the *process-level default* when per-org value is null.

**Risk:** Orgs currently relying on the global flag stay on it (null → fallback). An admin enabling per-org will be charged Bedrock cost — UI must show a cost warning. Mitigation: UI surfaces estimated added cost per 1k requests.

**Verification oracle (hard):**
1. Create two orgs A, B. Set org A `tier2_enabled=true`, B `tier2_enabled=false`. Process env `ENABLE_TIER2=false`.
2. Same Tier-2-only attack prompt (e.g., subtle prompt-extraction phrased neutrally to bypass Tier-1 regex) curl'd against both orgs' API keys.
3. Org A response must be **blocked** with `tier="tier_2"` in the audit event.
4. Org B response must **pass** (Tier-1 allows).
5. Verify MongoDB event for A has `bedrock_called=true`; for B has `bedrock_called=false`.

---

### Decision D-G3 — Bedrock circuit breaker for Tier-2

**Problem:** `scanner.py:768-785` silently falls back to Tier-1-only on any Bedrock failure (timeout, throttle, 5xx). There is no failure-rate tracking, no exponential backoff, no operator visibility. A 30-second AWS Bedrock outage today = 30 seconds of org-wide semantic-detection bypass with no signal.

**Decision:** Wrap Bedrock invocation in a circuit-breaker with three states:
- **CLOSED** (healthy): all Tier-2 calls go through; track success/failure ratio over a rolling 60-second window per (org, model).
- **OPEN** (breaker tripped): all Tier-2 calls return a synthetic `tier2_unavailable=true` verdict immediately. Behaviour governed by per-org `tier2_strict`:
  - If `tier2_strict=true`: action = **block** with reason_code `tier2_unavailable_strict`. Fail-closed.
  - If `tier2_strict=false` (default for back-compat): action = **flag** + audit event `tier2_degraded_pass`. The request proceeds with Tier-1-only.
- **HALF-OPEN** (after `breaker_cooldown_seconds`, default 30s): single probe request; success → CLOSED, failure → OPEN with backoff.

Failure threshold: 50% failure rate over a minimum of 5 calls in the rolling window. Configurable per-org via `tier2_breaker_threshold` (float 0–1) and `tier2_breaker_min_calls` (int).

Every state transition emits an operational `EnforcementEvent` (`event_class="tier2_breaker_state_change"`).

**Alternatives considered:**
1. **Always fail-closed on Bedrock error.** Rejected — first transient throttle blocks all production traffic; unacceptable blast radius.
2. **Always fail-open silently.** Rejected — this is the *current* behaviour and the reason G3 exists.
3. **Per-call retry with exponential backoff, no breaker.** Rejected — adds latency under degraded conditions, doesn't bound the failure rate, and conflicts with sync_pre_llm latency budget.
4. **Use an external library** (`pybreaker`, `circuitbreaker`). Considered — `pybreaker` is mature and tiny. Default proposal: use `pybreaker` if its license + footprint is acceptable; else hand-roll (~80 lines). Triage will decide.

**Files touched:**
- `gateway/ai_mesh_gateway/bedrock_circuit_breaker.py` (new) — circuit-breaker class with the three states, per-(org, model) keyed.
- `gateway/scanner.py:768-785` — wrap Bedrock call site.
- `gateway/main.py` — read per-org breaker config from `CONFIG_SYNC`.
- `backend/core/models.py` (or `GatewayConfig`) — `tier2_breaker_threshold`, `tier2_breaker_min_calls`, `tier2_breaker_cooldown_seconds`, `tier2_strict` (the last already added in G2). Migration.
- Optional dependency add to `gateway/pyproject.toml`: `pybreaker = "^1.0"` (triage decides).

**Risk:** Breaker state must not be process-local in a multi-replica deployment, else N replicas each independently learn the breaker. Initial implementation: in-process state (single gateway instance per cycle is the current deployment topology — verify with infra). Mark with a `# TODO(multi-replica): move breaker state to Redis with TTL` if not implementing now. Triage will challenge this.

**Verification oracle (hard):**
1. Inject Bedrock failure: monkeypatch (or use compose-level network drop) `bedrock_inference.py` client to raise 5xx for 60 seconds.
2. Send 10 Tier-2-required prompts within that window from org A (`tier2_strict=false`) — first ~5 see real Bedrock attempt, then breaker opens.
3. Verify MongoDB events: at least one `tier2_breaker_state_change` event with `to_state="OPEN"`; subsequent requests have `bedrock_called=false`, `event_class="tier2_degraded_pass"`.
4. Repeat with org B (`tier2_strict=true`) — requests after breaker opens must return 503 / block; audit events `tier2_unavailable_strict`.
5. Stop the injection; wait 30s; one probe should succeed; subsequent events show `to_state="CLOSED"`.

---

## 5. Phase 1 — decision sketches (lock in next session)

(Listed compact; will become full decisions with same depth before Phase 1 triage.)

- **D-G4:** Wire `model_downgrade` verdict — add a `routing_hints` dict to `request.state` populated by scanner; `llm_router.select_model()` consults hints; if `downgrade=true`, exclude high-capability models from candidate set (filter by `risk_score` threshold). Alternative: rewrite `body["model"]` directly (rejected: collides with kill-switch reroute).
- **D-G5:** Return rewritten query from `query_stage` and pass to retriever via stage-output contract; original query archived in audit event for forensics.
- **D-G6:** `ranker_stage` consults `policy.sensitive_fields` (a `list[str]`); for each retrieved doc, mask matching JSON paths (use existing JSONPath utility if present, else `glom`) before context assembly.
- **D-G7:** MCP tool-call handler in `mcp_connector/views.py` runs a redaction pass on the JSON-RPC `result.content` payload using policy-derived `redaction_fields` before returning to client.
- **D-G8:** Add `ContextAllowlist` model (org × user × tool × allowed-field-paths). Default deny if a tool has any allowlist row for the user; else allow (back-compat). UI: new "Agent Access" panel.
- **D-G9:** `circuit_breaker.py` emits `breaker_opened` signal; `kill_switch` listener auto-trips with `action="reroute"` and `fallback_model` derived from model config; emits `auto_trip` audit event.
- **D-G10:** `output_guard._grounding_score()` adds semantic mode — embeddings of response sentences and context chunks via existing `embedding_vault` (already in `query_stage.py`), cosine-similarity threshold configurable. Lexical mode remains fallback.

---

## 6. Phase 2 — decision sketches (lock in following session)

- **D-G11:** New Django view `EnforcementEventListView` with filter, search, pagination, CSV export. Frontend: new "Audit Log" tab in the existing dashboard. Pagination must be cursor-based for performance at scale.
- **D-G12:** Gateway middleware injects `X-Request-ID` header (uuid4) on entry, attaches via `request.state.request_id`, propagates to LiteLLM call (`extra_headers`), persists in every `EnforcementEvent` and telemetry event.
- **D-G13:** Wrap policy bundle Pub/Sub payload in AES-GCM using `POLICY_BUNDLE_KEY` (separate from `POLICY_SIGNING_KEY`; if absent, derive via HKDF from `POLICY_SIGNING_KEY`). Consumer decrypts then HMAC-verifies the inner. Document the layering.
- **D-G14:** Extend `_deobfuscate_text()`: strip zero-width chars (`\u200b\u200c\u200d\ufeff`), normalise unicode confusables via `unicodedata.normalize('NFKC', ...)`, iterate leet substitution up to 2 passes. Add adversarial test corpus.

---

## 7. Verification matrix (per gap)

| Gap | Unit test | Curl oracle | Playwright oracle | Audit artefact |
|---|---|---|---|---|
| G1 | HMAC verifier roundtrip + tamper | Tampered bundle → request still uses prior policies + alert event in Mongo | n/a | `policy_hmac_failure` event |
| G2 | per-org config resolution | Two orgs, same gateway, different verdicts on same payload | UI toggle persists | `bedrock_called` differs per org |
| G3 | breaker state machine | Bedrock 5xx injection → degraded_pass / strict_block | UI surfaces breaker state in admin panel | `tier2_breaker_state_change` events |
| G4 | router consumes hints | Scanner verdict `model_downgrade` → curl response served by low-risk model | n/a | `selected_model.risk_score < threshold` |
| G5 | query_stage returns rewritten | Curl with injection prefix → retriever telemetry shows sanitized query | n/a | event has `original_query` and `rewritten_query` |
| G6 | ranker filter | Curl → response cites doc; sensitive_field value absent | n/a | event lists `masked_fields` |
| G7 | MCP redact pass | MCP tool call returns result; sensitive field present in upstream but absent in client response | UI tool-call trace shows redaction | event has `redacted_field_count` |
| G8 | allowlist enforcement | User-A allowed tool → 200; User-B denied → 403 | UI agent-access editor | `allowlist_denied` event |
| G9 | breaker → kill-switch wiring | Repeated 5xx from model X → kill_switch key written; next request routed to fallback | UI shows kill-switch trip | `auto_trip` event |
| G10 | grounding cosine vs lexical | Paraphrased-but-grounded answer → semantic grounding ≥ 0.7 even when lexical < 0.3 | n/a | event has `grounding_mode="semantic"` and `grounding_score` |
| G11 | list endpoint pagination | `GET /api/enforcement-events?cursor=…` | UI log viewer | n/a (consumes them) |
| G12 | middleware injection | `X-Request-ID` echoed back; same ID present in every downstream event | UI shows trace ID | events linked by request_id |
| G13 | encrypt+decrypt roundtrip | `redis-cli GET policies:compiled:…` returns ciphertext (no plaintext) | n/a | n/a |
| G14 | obfuscation corpus pass rate | Adversarial prompts → block on Tier-1 alone | n/a | event lists deobfuscation steps |

---

## 8. What we are *not* doing (explicit non-goals)

- No new vector DB provider (Qdrant, Weaviate). Three is enough.
- No commercial guardrails integration (Lakera, NeMo). Tier-2 stays on Bedrock.
- No new model provider beyond what LiteLLM already supports. The router doesn't need more SDKs to be Module-1-complete.
- No multi-replica gateway state migration (defer to a separate scaling work item).
- No human-review queue UI for hallucinations in Phase 1 — G10 only improves the *score*. Queue UI is a separate workstream.
- No PII detection upgrade to Presidio. Existing regex + COMPLIANCE_TAG_MAP is sufficient; G14 hardens it.

---

## 9. Risks to the roadmap itself

- **Bedrock cost** if G2 + G3 lead more orgs to enable Tier-2. Mitigation: cost telemetry first, defaults stay conservative.
- **Migration risk** for G1 in deployments without `POLICY_SIGNING_KEY` set. Mitigation: ship behind `POLICY_REQUIRE_HMAC=false` for one release with prominent warning.
- **Triage churn** — if 5 contradicting agents repeatedly oppose a decision, ship-list shrinks. Acceptable; the contract requires reconciliation, not consensus.
- **Single-developer single-session execution** is unrealistic for all 14. The roadmap is explicit: only Phase 0 ships this session.

---

## 10. Open questions to resolve before triage

1. Should `POLICY_REQUIRE_HMAC` default `true` for new deployments and `false` for upgrades (env-detected), or hard-default `true` everywhere and require explicit opt-out? *(Triage Pragmatist will likely push for the latter.)*
2. Is `pybreaker` acceptable as a new dependency, or in-house breaker? *(Independence Purist will push for in-house.)*
3. Is per-(org, model) breaker keying correct, or should it be per-model-globally? *(Performance Hawk will argue model-global to share warm signal.)*
4. Where does the operational alert go beyond the `EnforcementEvent` table? Pager / webhook / Slack? *(Out of scope for Phase 0; capture as Phase 2 follow-on.)*

---

**Status:** This document is the input to the Phase 0 triage. Next action: dispatch 5 contradicting subagents on Decisions D-G1, D-G2, D-G3 simultaneously, wait for all five, reconcile into `final_decision_phase0.md`, only then touch code.
