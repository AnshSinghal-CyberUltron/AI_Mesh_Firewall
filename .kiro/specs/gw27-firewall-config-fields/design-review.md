# GW27 Design Review (revision pass 3)

Reviewed: `design.md` against `requirements.md`, with codebase reads for assumption checks. No build or test suites were run.

Verdict: **CHANGES_REQUESTED** (0 HIGH, 6 MEDIUM, 2 NIT).

The two HIGH findings from pass 2 (per-worker TPM false-429 and the cross-replica claim) are resolved. Tolerance floor, per-replica semantics and bucket parameters are now explicit. The remaining findings are narrower gaps in the new text.

## Coverage check (all 45 fields)

- `firewall_v2_map.py` has 46 `{"kind": "unsupported"}` entries (counted). The design names all of them.
- BUILT, 15: the section 2 table lists 15 rows, each with Published-as, Enforced-in and Mapped-in. The count check (4+1+1+2+1+3+3) holds. `blocked_keywords` maps through `firewall_v2_publish.keyword_row`, not the map module, and the `FIELDS` entry carries `via: rules.keywords`. That is acceptable and stated.
- RETIRED, 30: the section 13 table sums to 30 (1+1+1+2+2+1+2+3+1+4+6+6). Each row has an owner and reason. `org_token_budget` is the 46th, delegated to GW36.
- No silent drop. The drift test, committed ledger, field-named error and `retired_unchanged` note make retirement explicit.

## Findings

### 1. MEDIUM. Fast path and slot rule disagree on a disabled rate (section 5)

Section 5 says only orgs whose controls "enable rate or TPM" occupy a slot. The fast path is `if controls.rate is None and controls.org_tpm_limit == 0: return None`. `RateControl` carries `enabled`, and `controls_for` publishes the object when the operator turns rate limiting off. `rate is not None` with `enabled=False` then bypasses the fast path, enters the fail-closed `try`, and can consume a slot. With a full table it would be refused with 429 although the operator disabled the control. `require_capacity` counting has the same ambiguity.

Fix: define one predicate and use it in all three places (fast path, slot allocation, boot count):

```python
def _controlled(c: OrgControls) -> bool:
    return (c.rate is not None and c.rate.enabled) or c.org_tpm_limit > 0
# check(): if not _controlled(controls): return None   # before the try
```

Add a test: `rate=RateControl(False, 1000, 150)`, `org_tpm_limit=0` with a full table is admitted and takes no slot.

### 2. MEDIUM. Whether `rate_limit_enabled=false` also disables org TPM is unspecified (sections 2, 3, 5; Req 3.2/3.3)

`rate_limit_enabled` is called the master switch for the "rate group", yet `org_tpm_limit` is published as `controls.org_tpm_limit` outside `controls.rate`. Req 3.3 says TPM is enabled by a positive value alone. Req 3.2 says disabled means no rate rule. An implementer can reasonably gate TPM on the switch or not, and the two readings differ in production.

Fix: write one sentence in section 5 and the ledger `via` text. Recommended wording: "`org_tpm_limit > 0` is enforced independently of `rate_limit_enabled`; the switch gates only the request-rate and burst bucket." Add a test row for `(rate off, tpm 1000)` that expects the TPM bucket to apply.

### 3. MEDIUM. The retention sweeper has no `ResourceContract` (section 8, Data Models)

`retention_hard_floor_days`, `retention_sweep_batch` and `retention_sweep_period_s` come from `derive_org_bounds(contract)`. `derive_org_bounds` reads `workers()`, `metrics_directory_slots()` and `max_request_bytes()`, and raises `CapacityUnavailable` if `workers()` is unavailable. The host is `python -m audit_control` (`audit_control/service.py`), which today constructs no `ResourceContract` (grep shows none). The design does not say how the exporter obtains a contract, and a gateway-sized contract is irrelevant to an exporter process. If it is built the gateway way, the exporter can refuse to start for an unrelated reason.

Fix: split the derivation. Add `derive_retention_bounds(contract_or_none) -> RetentionBounds` in `runtime/resources_org.py` that needs no `workers()`. State how `service.py` builds its inputs: either a minimal contract constructor the exporter already can call, or named constants inside `resources_org.py`. Add a test that the exporter starts with `AMF_AUDIT_RETENTION_SWEEP=off` without any gateway worker configuration.

### 4. MEDIUM. Plumbing of `retention_days` into the sink is mis-described (section 8)

The design says `sink_pg.py` "writes `retention_days` from `record.detail.get("retention_days")`". The sink never sees an `AuditRecord`. `PendingRecord.columns()` parses the stored JSON payload, and the record's `detail` sits inside it as `body["detail"]`. A new column also requires changes to `RECORD_COLUMNS`, `_INSERT`, `columns()` and the positional tuple, and the schema must be applied before the new INSERT runs on an already-deployed table. None of that is listed.

Fix: add these items to step 7 of the implementation order:
- `RECORD_COLUMNS` and `_INSERT` gain `retention_days`.
- `columns()` reads `fields.get("detail", {}).get("retention_days")`, accepts an `int` (not `bool`) in `1..365`, else `None`.
- `schema.py` ALTER runs in the same `apply_schema` call before any INSERT, and a test replays an old-shape row (no `detail`) and expects NULL.
- Tests that pin `RECORD_COLUMNS` are updated.

### 5. MEDIUM. The partial index does not serve the sweep and enumeration queries (section 8)

The index is `(recorded_at_ns) WHERE retention_days IS NOT NULL`. The sweep predicate is `org_id = %(org)s AND recorded_at_ns < now_ns - retention_days * 86400e9`. The comparison uses a per-row expression, so the index range cannot bound it, and `org_id` is not in the index. Each org's batch walks every stamped row of every org. The enumeration query's claim that it is "served by the partial index" does not hold either (it scans the same set). On a large table the sweep becomes a repeated full index scan while holding row locks.

Fix: change the index to `(org_id, recorded_at_ns) WHERE retention_days IS NOT NULL`, and add a coarse bound that the index can use, using the maximum retention allowed by the control plane:

```sql
AND recorded_at_ns < %(now_ns)s - %(min_days)s::bigint * 86400000000000
```

where `min_days` is `retention_hard_floor_days`. Then the per-row expression only refines a bounded range. State the expected plan in the evidence (EXPLAIN on the live Postgres arm).

### 6. MEDIUM. False 429s under uneven worker spread are unbounded (section 5, request bucket)

The design splits rate and burst by `workers()` and accepts that "under uneven spread one connection sees `burst_worker`". Kernel accept distribution across workers is not uniform. At `W = 36`, `burst = 150` gives `burst_worker = 5`, but a burst of 150 spread randomly puts well over 5 requests on many workers (mean 4.2 per worker). So an org sending 150 requests, exactly at its limit, would see a substantial fraction refused. The TPM bucket received a tolerance floor for this reason (pass-2 finding 1); the request bucket did not.

Fix: either (a) add a slack to `burst_worker` derived in `resources_org.py` (a named multiplier over the mean, for example `ceil(burst / W * _BURST_SPREAD_MULT)`) and update the stated aggregate bound, or (b) state the accepted false-refusal envelope explicitly and add a property test that, for an org at `0.5 * burst` spread uniformly at random over W workers, the refusal probability is below a stated bound. Pick one and record it in D-4 and the ledger text. Without it, AC-4 can pass on `W = 1` and still reject compliant tenants in production.

### 7. NIT. Declared compliance framework with audit disabled is only reported (section 12, D-2)

`audit_logging_enabled = false` with HIPAA or PCI-DSS declared is accepted and surfaced in the publish note only. This is consistent with D-2 (declarative), and `_is_mandatory` still records blocks. Fix: state explicitly in D-2 that this is accepted by design, or add the pair to `built_value_errors` with a field-keyed refusal on `audit_logging_enabled`. Whichever is chosen, add one test.

### 8. NIT. Stamped `compliance_frameworks` adds per-record bytes (section 7)

Up to 16 labels of 32 characters (512 bytes) are stamped into `detail` on every record. R2-05's `ENTRY_FACTOR` and per-org byte model were calibrated on a 2.7 KB record. Fix: note the worst-case increase in the ledger and re-run the existing entry-size model gate with the stamped fields present, or stamp a short hash plus a lookup in the ledger.

## Verified Assumptions

- `ALLOWED_REL = "runtime/resources.py"` at `lint/check_capacity_literals.py:42` and the match at line 129 (`rel == ALLOWED_REL or rel.endswith("/" + ALLOWED_REL)`) are as the design states. `runtime/resources.py` is exactly 800 lines.
- `ResourceContract` has `workers`, `metrics_directory_slots`, `metrics_window_s`, `max_request_bytes`, `estimate_request_tokens`, `max_output_tokens`, `max_completions` and `default_response_token_ceiling`. `PlanStore.known()` exists (`plan/store.py:63`).
- `compile_plan(` call sites are `plan/delta.py:62`, `edge/dev_components.py:218`, `edge/dev_plan_feed.py:141`, plus the definition at `plan/compiler.py:125`. This matches the design's three.
- `posture.RATE_LIMITED` maps to 429 `rate_limit_error` in `edge/errors.py:279`. `rate_limited_verdict` is imported in `edge/quota_gate.py`.
- `AuditRecord.detail` is a `Mapping` ("the seam those fields arrive through"). `Category.EXFIL = "exfil"` and `_keyword_spans` exist. `Action` is a `StrEnum` including `flag`.
- `amf_audit_record` has `recorded_at_ns bigint NOT NULL` and primary key `(org_id, stream_id)`.
- `import-linter` `root_packages = ["gateway_v2"]`; `audit_control` and `state_control` sit outside it.
- No other file outside `firewall_v2_map.py` branches on `kind == "unsupported"`, so removing that kind is local to the map and its tests.

## Unverified / Wrong Assumptions

- Wrong or unspecified: the exporter can call `derive_org_bounds` (finding 3).
- Wrong: `sink_pg` can read `record.detail` (finding 4).
- Wrong: the partial index serves the sweep and enumeration queries (finding 5).
- Unverified: that per-worker `ceil(burst / W)` yields acceptable false-refusal rates under kernel accept distribution (finding 6).
- Unverified: `edge/openai_chat.py` is 712 lines (read from the design, not re-counted). Not blocking.
- Not checked: the truncated tail of `requirements.md` (AC-5 onward). The findings above do not depend on it.
