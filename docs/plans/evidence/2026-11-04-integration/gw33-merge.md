# GW33 output-parity merge into integration-gw27-35
- Merge commit: f58d8bf4 (git merge --no-ff gw33-output-parity onto GW31 note 183fcdf0)
- Fix commit: b89b62e8 (separate from the merge; size gate, ruff, GW27 tests that encoded a single-branch view)
- Handoff: `.agents/tasks/gw33/final-report.md` (live Docker/staging gates L33-* NOT run; exfil protection ships default-off, `V2_EXFIL_ORGS` empty, owner decision L-1).
## Conflicts + resolutions (14 files)
- control firewall_v2_map.py: GW27 FIELDS dispositions kept (retired/built); the five GW33 fields (`factuality_check_enabled`, three `hallucination_grounding_*`, `output_hallucination_action`) become `mapped`; `output_ip_leakage_action` loses its `reject`; `output_policy_action` keeps `built` + `reject: rewrite` (GW33 owner message). `selection_errors(incoming, stored, org_slug=None)` runs GW27's `_refusal` loop, then GW33's per-org rewrite gate + grounding validation (`_gw33_errors`, spec-level refusal wins). GW27 ledger helpers and GW33 helpers/`REWRITE_TEMPLATES` kept side by side.
- control firewall_v2_publish.py: `document_body(org_id, payload, rows, org_slug, *, controls_enabled, rules_mode)` = GW27 firewall + threat rows, GW33 platform rows, `controls`+`requires`, and GW33 strict-withhold promotion. `policy_drafts(policies, org_slug, *, output_*, monitor)`; `_policy_rule(..., org_slug, output)` accepts `rewrite` (template, per-org gated) through GW27's output-fallback table (`rewrite` is an own-action only, never a fallback). Console note keeps GW27 `_notes` and gains `streaming_mode_promoted`.
- control firewall_config_serializer.py: GW27 `built_value_errors` kept; `selection_errors` receives the org slug.
- plan/document.py: one `requires` list, `sorted(["controls/1", "rewrite/1"])` as each applies; `SUPPORTED_REQUIRES` is the union; GW33 error text kept; duplicate `_check_requires` call removed.
- plan/compiler.py: imports union (`Mapping`, `re`, `UNSET_CONTROLS`, `LEGACY_IP_RULE_PREFIX`, rag/controls/rewrite helpers). Hash keeps controls token, RAG non-default mix-in and the `rt:` rewrite-template part.
- runtime/resources.py: `ResourceContract(VectorBoundsMixin, RagBoundsMixin, RoutingBoundsMixin, IncidentBoundsMixin, OutputBoundsMixin)`; GW29 incident constants kept.
- lint/check_capacity_literals.py: `ALLOWED_RELS` frozenset gains `runtime/resources_output.py`.
- edge/routes.py: stream attempt = GW29 `open_chat_upstream(...)` + `mark_dispatched(routed_model)` wrapped in GW33 `bounded_upstream`; GW33's `_placeholder` dropped (GW31 moved it to `route_support`).
- edge/openai_chat.py: GW29 `ROUTING_UNKNOWN_MODEL` import + GW33 `inject_canary_for_plan`; controls noted before canary injection.
- edge/request_holds.py: slots/attrs union (`_attempt_*`, `_canary_ticket`, `_output_extras`).
- edge/audit_emit.py: `extra: Mapping[str, object]` with GW27's `stamp` kept.
- audit/record.py, domain/posture.py: imports/codes/`__all__` union.
- tests/gates/test_check_capacity.py: GW32 and GW33 cases kept; GW33 allowlist test is now a subset check.
## Fix commit b89b62e8
- edge/openai_chat.py: `run_lifecycle` was 125 lines (max 120); extracted `_pin_request` (clamp + controls + canary). Limit not raised.
- ruff: import order (compiler, resources), GW33 test I001/F401/UP041, file-level `E501` noqa on GW33 test files, two lines wrapped in existing tests. (GW33 only ran ruff on the package; tests were never linted on its branch.)
- GW27 tests encoding the single-branch view: capacity allowlist (+resources_output); dispositions 30 retired -> 25 retired + 5 GW33-mapped, `retire-list.json` regenerated from the map (built section untouched); rewrite is org-gated for four fields and always refused for `output_policy_action`; honest-save property ignores GW33-validated fields; non-served rewrite note now names its reason.
- test_gw33_canary_inject: `with_openai_body` now needs `model` and `org_ceiling` (GW29/GW36 signature).
## Gates (from gateway_v2/, venv /tmp/intv)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 4450 passed, 109 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector SDKs (baseline); 0 other (327 files) |
| lint-imports | 3 kept, 0 broken |
| check_sizes / http_outside_edge_resolve / no_module_mutable / capacity_literals / frozen_dataclasses / dispatch_provenance / single_authority / console_inventory x gateway_v2 | all exit 0 |
| check_secret_headers --repo .., check_credential_flow | exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140 (gateway_v2), state_control/valkey.py:100 |
Not run: control Django tests (the control image cannot migrate on SQLite, `near "EXISTS"`, reproduced on the base checkout; the control modules are covered by tests/plan GW27 and GW33 suites, which load them by path), GW33 live Docker/staging gate L33-*.
