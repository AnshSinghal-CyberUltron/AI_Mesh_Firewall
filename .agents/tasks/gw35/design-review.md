# GW35 design review, round 5
Design: `.kiro/specs/gw35-console-truth/design.md` (revision round 4). Requirements: `requirements.md`. Checked against the code in this worktree.
Round 4 findings 1 to 3 are resolved in the text: `output_action` carries dispositions only, rule 5(a) ignores `error`, `PLAN_UNAVAILABLE` is in `PREDISPATCH_OPERATIONAL`, and rule 4 has the no-input-decision `skip`/`skip` branch. Two new blocking problems remain. Verdict: CHANGES_REQUESTED (2 MEDIUM, 2 NIT).

## Findings

### 1. MEDIUM: the design edits two modules that are already exactly at the 800-line gate, with no size budget
Where: 5.7 (`egress/stream.py::_terminate`), D-26 and section 4 (`runtime/resources.py::unauth_audit_rate` plus its multiplier), 5.9, section 12 commit 2 and commit 5.
Code: `wc -l` gives `egress/stream.py` = 800 and `runtime/resources.py` = 800. `lint/check_sizes.py` fails when a module has more than 800 lines (`MAX_MODULE_LINES = 800`). The design budgets `routes.py` (799) in detail but is silent on these two. `_terminate` must replace `error != "killed"` and the `"block"` literal with `is_no_action(...)` and an operational/policy choice, and needs an import from `domain.failure_class`. `resources.py` gains a method, a docstring and a multiplier constant. Both exceed 800 and fail the gate in commit 2 and commit 5, which the design says must each be green.
Fix: add a size budget to 5.9 like the `routes.py` one. For `resources.py`, put `unauth_audit_rate()` and `_UNAUTH_AUDIT_RATE_MULT` in a new `runtime/resources_audit.py` (as `runtime/resources_vector.py` already exists for the same reason) and have `ResourceContract` delegate with a one-line method, or move an existing block out first. Re-count the net change and state the resulting line count. For `stream.py`, move the classification line into a small helper in `domain/failure_class.py` (`cut_output_action(error) -> str`) so `_terminate` changes by at most 0 net lines, and state which existing lines are removed. Add a per-commit `check_sizes` run to commits 2 and 5.

### 2. MEDIUM: `merge_row` rebuilds metadata from a fixed key list, so `refused_at` and `key_fingerprint` are dropped; D-21 totals cannot work
Where: 6.1 (`translate` writes `metadata["refused_at"]` and `metadata["key_fingerprint"]`), D-21, 6.3.
Code: `core/v2_audit_adapter.py::merge_row` builds `stored = {"source", "request_id", "phases"}` and then copies only `pipeline_trace`, `zeroshield` and `module_id`. Both ingest paths (`v2_audit_ingest.py:123,130`) store `merge_row`'s output. Any other key added by `translate` is silently discarded, so `refused_at` never reaches `EnforcementEvent.metadata` and the "not in `{auth, rate_limit}`" filter in D-21 has nothing to read. 6.1 says only that `merge_row` "keeps stricter wins".
Also unspecified: D-21 changes only `total_threats`. The sibling `blocked` in `security_views.py:~1385` still counts every `block` row, so a 401 probe (`auth: block`) is counted as a blocked threat, inconsistent with the stated intent. And the query that produces `shadow["total"]` must filter on a JSON key (`metadata__refused_at`), which the design does not specify.
Fix: state in 6.1 that `merge_row` carries `refused_at` (from the winning trace, recomputed from the stored trace so it matches `pipeline_trace`) and `key_fingerprint` into `stored`, and add a merge test asserting both survive for a single-record and a two-phase merge. State in 6.3 that `blocked`/`redacted` use the same `refused_at not in {auth, rate_limit}` exclusion (or say explicitly they do not, with the reason), and give the ORM expression (for example `.exclude(metadata__refused_at__in=["auth","rate_limit"])`). Rows written before this card have no `refused_at`; say they count as today.

### 3. NIT: three `PREDISPATCH_OPERATIONAL` codes cannot occur on the chat path
Where: D-17, section 11 `test_gw35_predispatch_failure.py`.
`credential_unavailable`, `provider_unreachable` and `store_not_configured` are raised only by the vector/RAG dispatch (`dispatch/vector_*`, `edge/openai_misc.py`), where `mark_dispatched` has its own semantics. The chat tests listed for them are synthetic `ChatFacts` cases. This is harmless but the text implies chat reachability.
Fix: add one sentence that these are defensive classifications for the shared table and the tests drive them through `build_stages` directly.

### 4. NIT: an input `redact` followed by an upstream `error` drops out of "threats"
Where: D-16, D-21.
The merged action is `error`, so a request whose PII was redacted before the provider failed is not counted in `redacted` or `total_threats`. This follows req 3.6 but loses a data-protection fact in the totals.
Fix: record it as a known gap in section 14, or count rows where any stage is `redact`/`flag`/`block`.

## Verified Assumptions
- `routes.py` is 799 lines, `openai_chat.py` 712; `check_sizes` limit is 800 modules and 120 per function.
- `_ProducedModeHolder` already carries `holds`; `StrictStats` has `output_action` and `error` slots, so `record_cut` can write them.
- Kill switch uses the literal `"killed"`; `_terminate` currently sets `output_action = "block"` for every non-`killed` error.
- `mark_dispatched` runs just before `provider.open` in non-stream (`openai_nonstream.py:198`) and stream (`routes.py:689`); in-stream `PLAN_UNAVAILABLE` is raised before it (`routes.py:682`).
- `run_with_no_splice` offers one attempt (`offered` latch), so write-once `note_failure` does not leak a failed first attempt into a successful retry.
- `mcp_console` calls `validate_console` and uses `ran=tool_called` on `tool_call`, as the design states.
- `hashed_bearer_key` returns a hash only for console-key shaped tokens.
- `v2_audit_adapter._RANK` is the 4-word map; `translate` uses `shown in _RANK`; `security_views.py:~1385` builds `gateway_v2` totals from `shadow`.

## Unverified / Wrong Assumptions
- Wrong: that the listed modules can absorb the new code within the size gate (finding 1).
- Wrong: that adding keys to `translate` metadata persists them (finding 2).
- Not verified: frontend line-level claims and the exact ORM shape behind `shadow`; the Django suites were not run.
