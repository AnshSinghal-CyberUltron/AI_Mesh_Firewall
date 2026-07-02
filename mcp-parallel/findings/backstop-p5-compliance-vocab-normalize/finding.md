# BACKSTOP hardening — MCP audit compliance-tag vocabulary unified (CHG-0059)

- **Item:** G2 item 5 ("Compliance tagging: extend mcp_compliance_tags.py to PII/IP/regulated;
  tag inputs + results; enforce by tag; audit"). Closes the vocab-mismatch residual tracked
  under CHG-0017 + CHG-0030.
- **Change-id:** CHG-0059 (2026-07-02)
- **Type:** Cross-plane audit-consistency defect (documented-contract violation), safe/additive fix.

## Gap
`MCPEvent.compliance_tags` is documented (models.py:309) as "a sorted list of ComplianceTag.code
values (e.g. ['GDPR-PII','PCI-CARD'])". Two planes write that field with DIFFERENT vocabularies:
- **Control enforcement path** (`views.py` `_record_event`, via `tags_for_preset_or_entity`)
  already emits CATALOG codes: `GDPR-PII / HIPAA-PHI / PCI-CARD / SOC2-CONF`.  ✅
- **Gateway scan path** (`patterns.py` `COMPLIANCE_TAG_MAP` / `get_compliance_tags`) emits a
  GRANULAR vocabulary: `GDPR / HIPAA / PII / PHI / PCI-DSS / SECRET / INFRA / SOC2`.  ❌ These
  are NOT `ComplianceTag.code` values.
So the SAME leak recorded via the two planes carries different tags (an SSN leak → control
`['GDPR-PII','HIPAA-PHI']` vs gateway `['GDPR','HIPAA','PII']`), breaking any group/filter/join by
tag and violating the field's own contract. (No live catalog-join exists yet — confirmed no
serializer/view/frontend does a `ComplianceTag` lookup; the frontend just displays the raw tags —
so this was a latent-but-real audit-integrity defect, not a runtime crash.)
Additionally the shared module had NO internal-infra keys at all (item 5's literal "extend to IP").

## Why the fix is at INGESTION, not at the gateway source
Prior iterations deferred "unify the vocab" as an owning-session decision because changing the
gateway's `COMPLIANCE_TAG_MAP` breaks 8 gateway tests + is cross-plane, and patterns.py is under
active concurrent edit (collision risk). The backstop insight: the field's CONTRACT is "catalog
codes", so the correct place to enforce it is the WRITE boundary — normalize gateway-granular tags
onto catalog codes at ingestion. This needs ZERO gateway changes (no test breakage, no collision).

## Fix
- `shared/ai_mesh_shared/mcp_compliance_tags.py`:
  - Extended `PRESET_TO_TAGS` with the gateway `detect_ip_leakage` keys
    (`internal_ipv4 / internal_hostname / internal_url / file_path_unix / file_path_windows /
    ip_leakage`) → `SOC2-CONF` (catalog's "internal credentials / confidential identifiers"
    bucket). Distinct from the generic public `ip_address` preset (personal data → GDPR-PII).
  - Added `to_catalog_codes(tags)` — maps granular tokens → catalog codes (GDPR/PII→GDPR-PII,
    HIPAA/PHI→HIPAA-PHI, PCI-DSS→PCI-CARD, SECRET/INFRA/SOC2→SOC2-CONF); IDEMPOTENT (catalog
    codes pass through), never drops a tag (unknown tokens pass through), skips non-strings,
    sorts+dedups. `CATALOG_TAG_CODES` mirrors control's `COMPLIANCE_TAG_CODES` (sync comment).
- `control/ai_mesh_control/mcp_connector/tasks.py`: `record_mcp_event_task` (gateway envelope
  ingestion) now normalizes `compliance_tags` via `to_catalog_codes` (exception-guarded — a tagging
  fault must never break audit recording).
- `control/ai_mesh_control/mcp_connector/views.py`: `_record_event` normalizes at persistence too
  (defense-in-depth; idempotent since it already emits catalog codes) — so EVERY MCPEvent write
  site upholds the catalog-code contract.

## No regression / no data loss
- `to_catalog_codes` never discards a compliance signal (unknown → passthrough); idempotent on
  already-catalog input, so control-path events are unchanged.
- Gateway `patterns.py` UNTOUCHED → the 8 gateway compliance tests + all others stay green.

## Verification
- Gateway (shared-module unit tests, pure-python):
  `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_compliance_tag_catalog_vocab.py -q`
  → 31 passed (infra→SOC2-CONF; every preset tag is a catalog code; granular→catalog incl. the live
  `['GDPR','HIPAA','PII']`→`['GDPR-PII','HIPAA-PHI']`; idempotency; unknown-preserved; empty/nonstring).
- Full gateway sweep → 1207 passed, 0 failed.
- Control (Django runner, throwaway container w/ working-tree bind-mount on the compose network+DB):
  `manage.py test mcp_connector.tests.test_compliance_tag_ingest_normalization` → 6 passed — a gateway
  envelope with `['GDPR','HIPAA','PII']` PERSISTS as `MCPEvent.compliance_tags == ['GDPR-PII','HIPAA-PHI']`;
  INFRA/SECRET→SOC2-CONF; PCI-DSS→PCI-CARD; catalog codes idempotent; unknown preserved; empty ok.
  Broader `mcp_connector` (test_scan_controls + test_oauth_transport_guard + new) → 21 passed, 0 fail
  (2 pre-existing harness errors — test_mcp_guardrail needs pytest, test_scan_version_bump needs
  fakeredis; unrelated to this change).

## Residuals (documented, not blocking item 5's tagging pipeline)
- `ITAR` / `FERPA` catalog codes have no detector/preset producing them (no in-scope data type maps
  there) — catalog-completeness note, nothing to normalize onto them.
- The gateway still EMITS granular vocab at source; consistency is enforced at the audit WRITE
  boundary (by design, to avoid the cross-plane test breakage). The gateway's live RESPONSE tags
  (zeroshield.compliance_tags) remain granular — a display surface, not the audit record.
- Ideal future: make `shared/ai_mesh_shared/mcp_compliance_tags.py::CATALOG_TAG_CODES` the single
  source of truth and have control's `policy/compliance_tags.py` import it (removes the sync-comment
  duplication).
